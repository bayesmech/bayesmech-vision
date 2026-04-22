# Google Auth Setup

The iOS app is wired to read Google Sign-In configuration from build settings and `Info.plist`.

## Important

`google-services.json` is an Android configuration file. It is not enough to finish iOS Google Sign-In by itself.

For iOS, we need either:

- the iOS OAuth client ID
- the reversed iOS client ID URL scheme
- the server/web client ID used by the backend

or an iOS `GoogleService-Info.plist` that contains those values.

## Repo-safe setup

1. Copy `BayesMechVision/Support/Configurations/Credentials.xcconfig.example`
   to `BayesMechVision/Support/Configurations/Credentials.xcconfig`
2. Fill in:
   - `GOOGLE_IOS_CLIENT_ID`
   - `GOOGLE_SERVER_CLIENT_ID`
   - `GOOGLE_REVERSED_CLIENT_ID`

The real `Credentials.xcconfig` is ignored by `ios/.gitignore`.
