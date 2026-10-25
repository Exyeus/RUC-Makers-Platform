from django.db import migrations


# Cross-event references must also fail for bulk SQL/imports that bypass API checks.
REFERENCES = {
    'participant': [('team_id', 'team')],
    'team': [('captain_id', 'participant'), ('track_id', 'track')],
    'matchingprofile': [('participant_id', 'participant')],
    'matchingcontact': [('participant_id', 'participant')],
    'matchingblock': [('source_id', 'participant'), ('target_id', 'participant')],
    'idea': [('author_id', 'participant')],
    'ideaimplementation': [('idea_id', 'idea'), ('team_id', 'team')],
    'matchintent': [('sender_id', 'participant'), ('recipient_id', 'participant'), ('team_id', 'team')],
    'teamproposal': [
        ('creator_id', 'participant'),
        ('target_team_id', 'team'),
        ('idea_id', 'idea'),
        ('track_id', 'track'),
    ],
    'proposalconsent': [('proposal_id', 'teamproposal'), ('participant_id', 'participant')],
    'teammembership': [('participant_id', 'participant'), ('team_id', 'team')],
    'matchingprocessedevent': [('source_id', 'matchingoutbox')],
    'recommendationrun': [('viewer_id', 'participant')],
    'matchingfeedback': [('participant_id', 'participant')],
}


FUNCTIONS = """
CREATE FUNCTION hack_matching_scope_guard() RETURNS trigger AS $$
DECLARE
    position integer;
    reference_id text;
    owner_id bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.hackathon_id IS DISTINCT FROM NEW.hackathon_id THEN
        RAISE EXCEPTION 'Event ownership is immutable' USING ERRCODE = '23514';
    END IF;
    FOR position IN 0..TG_NARGS / 2 - 1 LOOP
        reference_id := to_jsonb(NEW) ->> TG_ARGV[position * 2];
        IF reference_id IS NOT NULL THEN
            EXECUTE format('SELECT hackathon_id FROM %I WHERE id::text = $1', TG_ARGV[position * 2 + 1])
            INTO owner_id USING reference_id;
            IF owner_id IS DISTINCT FROM NEW.hackathon_id THEN
                RAISE EXCEPTION 'Cross-event reference rejected' USING ERRCODE = '23514';
            END IF;
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION hack_matching_capacity_guard() RETURNS trigger AS $$
DECLARE
    event_row hackathon_hackathon%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.team_id IS NOT DISTINCT FROM NEW.team_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO event_row FROM hackathon_hackathon WHERE id = NEW.hackathon_id FOR UPDATE;
    IF event_row.matching_enabled AND (
        event_row.matching_frozen_at IS NOT NULL OR clock_timestamp() >= event_row.team_freeze_at
    ) AND (NEW.team_id IS NOT NULL OR TG_OP = 'UPDATE') THEN
        RAISE EXCEPTION 'Roster is frozen' USING ERRCODE = '23514';
    END IF;
    IF NEW.team_id IS NOT NULL AND (
        SELECT count(*) FROM hackathon_participant WHERE team_id = NEW.team_id AND id <> NEW.id
    ) >= event_row.team_size THEN
        RAISE EXCEPTION 'Team capacity exceeded' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def install(apps, schema_editor):
    schema_editor.execute("""
        INSERT INTO hackathon_teammembership (created, updated, hackathon_id, participant_id, team_id, joined_at)
        SELECT CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, hackathon_id, id, team_id, CURRENT_TIMESTAMP
        FROM hackathon_participant WHERE team_id IS NOT NULL
    """)
    schema_editor.execute(FUNCTIONS, params=None)
    for table, references in REFERENCES.items():
        args = ', '.join(f"'{column}', 'hackathon_{target}'" for column, target in references)
        schema_editor.execute(
            f'CREATE TRIGGER matching_scope BEFORE INSERT OR UPDATE ON hackathon_{table} '
            f'FOR EACH ROW EXECUTE FUNCTION hack_matching_scope_guard({args})'
        )
    schema_editor.execute(
        'CREATE TRIGGER matching_capacity BEFORE INSERT OR UPDATE OF team_id ON hackathon_participant '
        'FOR EACH ROW EXECUTE FUNCTION hack_matching_capacity_guard()'
    )


def uninstall(apps, schema_editor):
    schema_editor.execute('DROP TRIGGER matching_capacity ON hackathon_participant')
    for table in REFERENCES:
        schema_editor.execute(f'DROP TRIGGER matching_scope ON hackathon_{table}')
    schema_editor.execute('DROP FUNCTION hack_matching_scope_guard()')
    schema_editor.execute('DROP FUNCTION hack_matching_capacity_guard()')


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0006_hackathon_matching_cooldown_hours_and_more')]
    operations = [migrations.RunPython(install, uninstall)]
