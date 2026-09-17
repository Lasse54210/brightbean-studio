"""Abort abandoned multipart uploads in the media bucket (Blue Monkey Media fork).

The recurring background task does the same thing daily; this exists for the
first run on an installation that already has a backlog, and for checking
with ``--dry-run`` what a sweep would do before it does it.

Usage:
    python manage.py abort_stale_multipart_uploads --dry-run
    python manage.py abort_stale_multipart_uploads
    python manage.py abort_stale_multipart_uploads --older-than-hours 48
"""

from django.core.management.base import BaseCommand

from apps.media_library.multipart_sweep import STALE_AFTER, abort_stale_multipart_uploads
from apps.media_library.storage import is_s3_backend


class Command(BaseCommand):
    help = "Abort multipart uploads that were started, never finished, and are not claimed by a live upload session."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would be aborted without aborting.")
        parser.add_argument(
            "--older-than-hours",
            type=float,
            default=STALE_AFTER.total_seconds() / 3600,
            help=f"Only uploads started more than N hours ago (default: {STALE_AFTER.total_seconds() / 3600:g}).",
        )

    def handle(self, *args, **options):
        if not is_s3_backend():
            self.stdout.write("Storage backend is not S3; there are no multipart uploads to sweep.")
            return
        from datetime import timedelta

        summary = abort_stale_multipart_uploads(
            older_than=timedelta(hours=options["older_than_hours"]),
            dry_run=options["dry_run"],
            log=self.stdout.write,
        )
        verb = "would be aborted" if summary["dry_run"] else "aborted"
        self.stdout.write(
            f"{summary['open']} open, {summary['aborted']} {verb}, "
            f"{summary['kept_live']} kept (live session), {summary['kept_young']} kept (too young)."
        )
