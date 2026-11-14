"""Bind organizer decisions to the exact version they actually reviewed."""

import hashlib
import json


def fingerprint(project):
    values = {
        name: getattr(project, name)
        for name in (
            'revision',
            'state',
            'state_reason',
            'review_note',
            'equipment_checked',
            'award',
            'submission_manifest_hash',
        )
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
