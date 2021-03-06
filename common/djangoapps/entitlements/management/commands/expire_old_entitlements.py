"""
Management command for expiring old entitlements.
"""

from __future__ import absolute_import

import logging

from django.core.management import BaseCommand
from six.moves import range

from entitlements.models import CourseEntitlement
from entitlements.tasks import expire_old_entitlements

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Command(BaseCommand):
    """
    Management command for expiring old entitlements.

    Most entitlements get expired as the user interacts with the platform,
    because the LMS checks as it goes. But if the learner has not logged in
    for a while, we still want to reap these old entitlements. So this command
    should be run every now and then (probably daily) to expire old
    entitlements.

    The command's goal is to pass a narrow subset of entitlements to an
    idempotent Celery task for further (parallelized) processing.
    """
    help = 'Expire old entitlements.'

    def add_arguments(self, parser):
        parser.add_argument(
            '-c', '--commit',
            action='store_true',
            default=False,
            help='Submit tasks for processing'
        )

        parser.add_argument(
            '--batch-size',
            type=int,
            default=10000,  # arbitrary, should be adjusted if it is found to be inadequate
            help='How many entitlements to give each celery task'
        )

    def handle(self, *args, **options):
        logger.info('Looking for entitlements which may be expirable.')

        total = CourseEntitlement.objects.count()
        batch_size = max(1, options.get('batch_size'))
        num_batches = ((total - 1) / batch_size + 1) if total > 0 else 0

        if options.get('commit'):
            logger.info('Enqueuing %d entitlement expiration tasks.', num_batches)
        else:
            logger.info(
                'Found %d batches. To enqueue entitlement expiration tasks, pass the -c or --commit flags.',
                num_batches
            )
            return

        for batch_num in range(num_batches):
            start = batch_num * batch_size + 1  # ids are 1-based, so add 1
            end = min(start + batch_size, total + 1)
            expire_old_entitlements.delay(start, end, logid=str(batch_num))

        logger.info('Done. Successfully enqueued %d tasks.', num_batches)
