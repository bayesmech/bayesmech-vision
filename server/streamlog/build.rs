use std::{env, path::PathBuf};

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let proto_dir = crate_dir.join("../../proto");

    println!("cargo:rerun-if-changed={}", proto_dir.display());

    let protos = [
        "primitives.proto",
        "spatial.proto",
        "perceiver.proto",
        "segmentation.proto",
        "motioncap.proto",
        "idoslam.proto",
        "pongtown.proto",
        "insightgen.proto",
        "reconstruction.proto",
        "snookestown.proto",
        "position.proto",
    ]
    .into_iter()
    .map(|name| proto_dir.join(name))
    .collect::<Vec<_>>();

    prost_build::Config::new()
        .bytes(["."])
        .compile_protos(&protos, &[proto_dir])
        .expect("failed to compile protobuf schemas");
}
