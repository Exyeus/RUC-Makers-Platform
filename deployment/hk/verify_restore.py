"""Check migrated counts and rollback-only activity creation on the restored database."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from django_scopes import scopes_disabled
from eventyay.base.models import Event, Organizer, User
from eventyay.hackathon.models import (
    Asset,
    Hackathon,
    Participant,
    Project,
    StudentAccount,
    Team,
)

root = Path("/srv/ruc-makers")
snapshot = json.loads((root / "runtime/migration-input/source-counts.json").read_text())
expected = snapshot["counts"]


def digest(rows):
    return hashlib.sha256(
        json.dumps(
            list(rows), sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


with scopes_disabled():
    counts = {
        model.__name__: model.objects.count()
        for model in [Hackathon, Participant, Team, Project, Asset, User]
    }
    assert counts == expected, (counts, expected)
    if "identity_digests" in snapshot:
        assert (
            digest(User.objects.order_by("pk").values("pk", "password", "is_active"))
            == snapshot["identity_digests"]["users"]
        )
        assert (
            digest(
                StudentAccount.objects.order_by("pk").values(
                    "pk", "user_id", "student_id"
                )
            )
            == snapshot["identity_digests"]["student_accounts"]
        )
    assets = {
        str(asset.pk): {
            "file": asset.file.name,
            "size": asset.size,
            "checksum": asset.checksum,
        }
        for asset in Asset.objects.all()
    }
for original in json.loads(
    (root / "runtime/migration-input/asset-manifest.json").read_text()
):
    assert assets[str(original["pk"])] == {
        key: original[key] for key in ["file", "size", "checksum"]
    }
with transaction.atomic():
    suffix = uuid4().hex[:12]
    organizer = Organizer.objects.create(
        name="Migration acceptance", slug="migration-" + suffix
    )
    event = Event.objects.create(
        organizer=organizer,
        name="Migration acceptance",
        slug="migration-" + suffix,
        date_from=timezone.now(),
        timezone="Asia/Shanghai",
        currency="CNY",
    )
    now = timezone.now()
    activity = Hackathon.objects.create(
        event=event,
        slug="migration-" + suffix,
        name="Migration acceptance",
        registration_deadline=now + timedelta(days=1),
        team_freeze_at=now + timedelta(days=2),
        soft_deadline=now + timedelta(days=3),
        submission_deadline=now + timedelta(days=4),
        freeze_at=now + timedelta(days=5),
    )
    assert activity.program == Hackathon.Program.MAKER_CAMP
    transaction.set_rollback(True)
with scopes_disabled():
    assert {
        model.__name__: model.objects.count()
        for model in [Hackathon, Participant, Team, Project, Asset, User]
    } == expected
result = {
    "counts_match": counts,
    "asset_metadata_unchanged": True,
    "original_account_passwords_and_student_mappings": "PASS"
    if "identity_digests" in snapshot
    else "not captured",
    "create_activity_rollback": "PASS",
}
(root / "runtime/verification/staging-restore.json").write_text(
    json.dumps(result, indent=2)
)
print(json.dumps(result))
