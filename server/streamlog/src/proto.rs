#[allow(clippy::derive_partial_eq_without_eq)]
#[allow(clippy::large_enum_variant)]
#[allow(clippy::too_many_arguments)]
pub mod bayesmech {
    pub mod vision {
        include!(concat!(env!("OUT_DIR"), "/bayesmech.vision.rs"));
    }
}

pub use bayesmech::vision::*;
