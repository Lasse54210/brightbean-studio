"""Link inbox messages that arrived before their post id was understood.

``related_post`` is filled at the moment a message lands, by the poll and by
the webhook. Until LinkedIn's ``post_urn`` and YouTube's ``video_id`` were part
of the lookup, both providers stored every comment with a NULL ``related_post``
even though the matching ``PlatformPost`` was right there. This walks what is
already in the database and links what can be linked.

It never unlinks. A message that already points at a post keeps pointing at it,
so running this twice costs a query and changes nothing.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.composer.models import PlatformPost
from apps.inbox.models import InboxMessage
from apps.inbox.tasks import _related_post_key


class Command(BaseCommand):
    help = "Link inbox messages to the post they hang off, for messages stored without one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be linked and change nothing.",
        )
        parser.add_argument(
            "--platform",
            type=str,
            default=None,
            help="Only this platform (e.g. linkedin, youtube).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        platform = options["platform"]

        messages = InboxMessage.objects.filter(related_post__isnull=True).select_related("social_account")
        if platform:
            messages = messages.filter(social_account__platform=platform)

        # The key is per account: two accounts can hold the same post id, and
        # a message must never be linked to another account's post.
        wanted: dict[tuple[str, str], list[InboxMessage]] = {}
        no_key = 0
        for message in messages.iterator(chunk_size=500):
            key = _related_post_key(message.extra)
            if not key:
                no_key += 1
                continue
            wanted.setdefault((str(message.social_account_id), key), []).append(message)

        if not wanted:
            self.stdout.write(f"Nothing to link. {no_key} messages carry no post id (a DM has none).")
            return

        account_ids = {account_id for account_id, _ in wanted}
        keys = {key for _, key in wanted}
        posts = {
            (str(social_account_id), platform_post_id): pk
            for social_account_id, platform_post_id, pk in PlatformPost.objects.filter(
                social_account_id__in=account_ids,
                platform_post_id__in=keys,
            ).values_list("social_account_id", "platform_post_id", "id")
        }

        to_link = []
        for pair, group in wanted.items():
            post_pk = posts.get(pair)
            if post_pk is None:
                continue
            for message in group:
                message.related_post_id = post_pk
                to_link.append(message)

        unmatched = sum(len(group) for pair, group in wanted.items() if pair not in posts)

        by_platform: dict[str, int] = {}
        for message in to_link:
            name = message.social_account.platform
            by_platform[name] = by_platform.get(name, 0) + 1
        for name in sorted(by_platform):
            self.stdout.write(f"  {name}: {by_platform[name]}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {len(to_link)} messages would be linked, "
                    f"{unmatched} carry a post id this instance never published, "
                    f"{no_key} carry none at all."
                )
            )
            return

        with transaction.atomic():
            InboxMessage.objects.bulk_update(to_link, ["related_post"], batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Linked {len(to_link)} messages. "
                f"{unmatched} carry a post id this instance never published, "
                f"{no_key} carry none at all."
            )
        )
