use std::net::SocketAddr;

use anyhow::Context;
use tokio::net::TcpListener;
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use streamlog::{config::Config, routes, state::AppState};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("streamlog=info,tower_http=info,axum=info"));
    tracing_subscriber::registry()
        .with(filter)
        .with(tracing_subscriber::fmt::layer())
        .init();

    let config = Config::load().context("failed to load streamlog config")?;
    config.ensure_dirs().await?;
    config.log_sanitized();

    let state = AppState::new(config.clone()).await?;
    let app = routes::router(state)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());

    let listener_cfg = config.default_listener();
    let addr: SocketAddr = format!("{}:{}", listener_cfg.bind_host, listener_cfg.bind_port)
        .parse()
        .context("invalid streamlog bind address")?;
    let listener = TcpListener::bind(addr).await?;
    info!(
        listener = listener_cfg.name,
        bind = %addr,
        base_url = listener_cfg.base_url,
        "streamlog listener started"
    );

    axum::serve(listener, app.into_make_service())
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl-C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
