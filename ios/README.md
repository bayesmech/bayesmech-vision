# BayesMech Vision iOS

This directory contains the native iOS port scaffold for BayesMech Vision.

## Constraints

- Bundle identifier: `com.bayesmech.vision`
- Deployment target: `iOS 18.0`
- Intended hardware floor: `iPhone 17 and newer`
- LiDAR depth support is required
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

## Google Sign-In

Fill in iOS Google Sign-In credentials via:

- `BayesMechVision/Support/Configurations/Credentials.xcconfig`

Start from:

- `BayesMechVision/Support/Configurations/Credentials.xcconfig.example`

Details are in:

- `ios/docs/AUTH_SETUP.md`

## Layout References

Exact copies of the Android XML layouts used as parity references live in:

- `ios/ParityReference/AndroidLayouts/`

These are not compiled by iOS, but they preserve the current Android visual/source-of-truth while the UIKit implementation catches up.
