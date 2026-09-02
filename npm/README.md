# npm Distribution Workspace

This workspace owns the npm launcher packages and platform runtime packages.

The package contains an `applications` runtime mirror. The `mind` command locates the bundled Mind executable there, then starts it from the caller's current working directory.

Release and publishing procedures are documented in [`PUBLISHING.md`](PUBLISHING.md).
