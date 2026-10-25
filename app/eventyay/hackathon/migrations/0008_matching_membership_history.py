from django.db import migrations


SQL = """
CREATE FUNCTION hack_matching_history_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.team_id IS NOT DISTINCT FROM NEW.team_id THEN
        RETURN NEW;
    END IF;
    UPDATE hackathon_teammembership SET left_at = clock_timestamp(), updated = clock_timestamp()
    WHERE participant_id = NEW.id AND left_at IS NULL AND team_id IS DISTINCT FROM NEW.team_id;
    IF NEW.team_id IS NOT NULL THEN
        INSERT INTO hackathon_teammembership
            (created, updated, hackathon_id, participant_id, team_id, joined_at)
        SELECT clock_timestamp(), clock_timestamp(), NEW.hackathon_id, NEW.id, NEW.team_id, clock_timestamp()
        WHERE NOT EXISTS (
            SELECT 1 FROM hackathon_teammembership WHERE participant_id = NEW.id AND left_at IS NULL
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER matching_history AFTER INSERT OR UPDATE OF team_id ON hackathon_participant
FOR EACH ROW EXECUTE FUNCTION hack_matching_history_guard();
"""


class Migration(migrations.Migration):
    dependencies = [('hackathon', '0007_matching_integrity')]
    operations = [
        migrations.RunSQL(
            SQL,
            'DROP TRIGGER matching_history ON hackathon_participant; DROP FUNCTION hack_matching_history_guard();',
        )
    ]
