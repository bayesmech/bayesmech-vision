# BayesMech Vision iOS

This directory contains the native iOS port scaffold for BayesMech Vision.

## Constraints

- Bundle identifier: `com.bayesmech.vision`
- No changes to repo-level `proto/`
- No changes to `dashboard/`
- iOS outputs and references live entirely under `ios/`

## Tooling

- Project generation: `xcodegen`
- Proto generation: `protoc` + `protoc-gen-swift`
- Dependencies:
  - `SwiftProtobuf`
  - `GoogleSignIn`

## Bootstrap

```bash
cd ios
./scripts/generate_protos.sh
xcodegen generate
```

Open `BayesMechVision.xcodeproj` in Xcode on a machine with the full iOS SDK installed.

## Layout References

Exact copies of the Android XML layouts used as parity references live in:

- `ios/ParityReference/AndroidLayouts/`

These are not compiled by iOS, but they preserve the current Android visual/source-of-truth while the UIKit implementation catches up.
