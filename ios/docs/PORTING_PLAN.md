# iOS Porting Plan

This directory tracks the native iOS port under the hard constraints requested by the team.

## Immutable Contracts

- Bundle identifier stays `com.bayesmech.vision`
- Deployment target is pinned to `iOS 18.0`
- Hardware target is `iPhone 17 and newer`
- LiDAR-backed scene depth is required
- Repo-level `.proto` files stay unchanged
- Existing dashboard stays unchanged
- Existing Python/FastAPI endpoints stay unchanged
- Existing `.pb` recording framing stays unchanged

## Source of Truth

- Android UX and layout behavior under `android/app/src/main`
- Shared protocol definitions under `proto/`
- Existing server endpoints used by the Android app

## Implementation Tracks

1. Proto and transport parity
2. Recording and cache parity
3. ARKit capture pipeline parity
4. Motion/location parity
5. Login/library/settings parity
6. Analysis/chat parity
7. Visual parity and asset replacement

## Known Inputs Still Needed

- Production iOS OAuth client ID and reversed URL scheme for Google Sign-In
