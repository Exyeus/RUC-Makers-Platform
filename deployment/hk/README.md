# Hong Kong deployment

The application runs behind Nginx using systemd, PostgreSQL and Redis. Keep environment, credentials, backups and operator handoff private. The S3-compatible storage backend requires separately installed dependencies and refreshed temporary credentials. Preserve the original private deployment patch and validate restore and authorization before deployment.
