# Private asset storage

Hackathon assets can use local storage or an S3-compatible backend. Configure endpoint, bucket, prefix and credentials privately with EVY_HACKATHON_S3_* settings. Never commit credentials or deployment-specific configuration. Temporary credentials must be refreshed before expiry. Back up the database and asset objects together; test restore and authorization before production use. See the private operator handoff for deployment-specific procedures.
