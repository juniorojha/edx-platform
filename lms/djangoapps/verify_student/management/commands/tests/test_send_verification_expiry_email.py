"""
Tests for django admin command `send_verification_expiry_email` in the verify_student module
"""

from __future__ import absolute_import

from datetime import timedelta

import boto
from django.conf import settings
from django.contrib.sites.models import Site
from django.core import mail
from django.core.management import call_command
from django.utils.timezone import now
from mock import patch
from student.tests.factories import CourseEnrollmentFactory, UserFactory
from testfixtures import LogCapture
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from common.test.utils import MockS3Mixin
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification
from lms.djangoapps.verify_student.tests.test_models import FAKE_SETTINGS, mock_software_secure_post

LOGGER_NAME = 'lms.djangoapps.verify_student.management.commands.send_verification_expiry_email'


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@patch('lms.djangoapps.verify_student.models.requests.post', new=mock_software_secure_post)
class TestSendVerificationExpiryEmail(MockS3Mixin, ModuleStoreTestCase):
    """ Tests for django admin command `send_verification_expiry_email` in the verify_student module """

    def setUp(self):
        """ Initial set up for tests """
        super(TestSendVerificationExpiryEmail, self).setUp()
        connection = boto.connect_s3()
        connection.create_bucket(FAKE_SETTINGS['SOFTWARE_SECURE']['S3_BUCKET'])
        Site.objects.create(domain='edx.org', name='edx.org')
        self.resend_days = settings.VERIFICATION_EXPIRY_EMAIL['RESEND_DAYS']
        self.days = settings.VERIFICATION_EXPIRY_EMAIL['DAYS_RANGE']
        self.default_no_of_emails = settings.VERIFICATION_EXPIRY_EMAIL['DEFAULT_EMAILS']

    def create_and_submit(self, user):
        """ Helper method that lets us create new SoftwareSecurePhotoVerifications """
        attempt = SoftwareSecurePhotoVerification(user=user)
        attempt.upload_face_image("Fake Data")
        attempt.upload_photo_id_image("More Fake Data")
        attempt.mark_ready()
        attempt.submit()
        return attempt

    def test_expiry_date_range(self):
        """
        Test that the verifications are filtered on the given range. Email is not sent for any verification with
        expiry date out of range
        """
        user = UserFactory.create()
        verification_in_range = self.create_and_submit(user)
        verification_in_range.status = 'approved'
        verification_in_range.expiry_date = now() - timedelta(days=self.days)
        verification_in_range.save()

        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.days + 1)
        verification.save()

        call_command('send_verification_expiry_email')

        # Check that only one email is sent
        self.assertEqual(len(mail.outbox), 1)

        # Verify that the email is not sent to the out of range verification
        expiry_email_date = SoftwareSecurePhotoVerification.objects.get(pk=verification.pk).expiry_email_date
        self.assertIsNone(expiry_email_date)

    def test_expiry_email_date_range(self):
        """
        Test that the verifications are filtered if the expiry_email_date has reached the time specified for
        resending email
        """
        user = UserFactory.create()
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        verification_in_range = self.create_and_submit(user)
        verification_in_range.status = 'approved'
        verification_in_range.expiry_date = today - timedelta(days=self.days + 1)
        verification_in_range.expiry_email_date = today - timedelta(days=self.resend_days)
        verification_in_range.save()

        call_command('send_verification_expiry_email')

        # Check that email is sent even if the verification is not in expiry_date range but matches the criteria
        # to resend email
        self.assertEqual(len(mail.outbox), 1)

    def test_most_recent_verification(self):
        """
        Test that the SoftwareSecurePhotoVerification object is not filtered if it is outdated. A verification is
        outdated if it's expiry_date and expiry_email_date is set NULL
        """
        # For outdated verification the expiry_date and expiry_email_date is set NULL verify_student/views.py:1164
        user = UserFactory.create()
        outdated_verification = self.create_and_submit(user)
        outdated_verification.status = 'approved'
        outdated_verification.save()

        # Check that the expiry_email_date is not set for the outdated verification
        expiry_email_date = SoftwareSecurePhotoVerification.objects.get(pk=outdated_verification.pk).expiry_email_date
        self.assertIsNone(expiry_email_date)

    def test_send_verification_expiry_email(self):
        """
        Test that checks for valid criteria the email is sent and expiry_email_date is set
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.days)
        verification.save()

        call_command('send_verification_expiry_email')

        expected_date = now()
        attempt = SoftwareSecurePhotoVerification.objects.get(user_id=verification.user_id)
        self.assertEquals(attempt.expiry_email_date.date(), expected_date.date())
        self.assertEqual(len(mail.outbox), 1)

    def test_email_already_sent(self):
        """
        Test that if email is already sent as indicated by expiry_email_date then don't send again if it has been less
        than resend_days
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.days)
        verification.expiry_email_date = now()
        verification.save()

        call_command('send_verification_expiry_email')

        self.assertEqual(len(mail.outbox), 0)

    def test_no_verification_found(self):
        """
        Test that if no approved and expired verifications are found the management command terminates gracefully
        """
        start_date = now() - timedelta(days=self.days)  # using default days
        with LogCapture(LOGGER_NAME) as logger:
            call_command('send_verification_expiry_email')
            logger.check(
                (LOGGER_NAME,
                 'INFO', u"No approved expired entries found in SoftwareSecurePhotoVerification for the "
                         u"date range {} - {}".format(start_date.date(), now().date()))
            )

    def test_dry_run_flag(self):
        """
        Test that the dry run flags sends no email and only logs the the number of email sent in each batch
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.days)
        verification.save()

        start_date = now() - timedelta(days=self.days)  # using default days
        count = 1

        with LogCapture(LOGGER_NAME) as logger:
            call_command('send_verification_expiry_email', '--dry-run')
            logger.check(
                (LOGGER_NAME,
                 'INFO',
                 u"For the date range {} - {}, total Software Secure Photo verification filtered are {}"
                 .format(start_date.date(), now().date(), count)
                 ),
                (LOGGER_NAME,
                 'INFO',
                 u"This was a dry run, no email was sent. For the actual run email would have been sent "
                 u"to {} learner(s)".format(count)
                 ))
        self.assertEqual(len(mail.outbox), 0)

    def test_not_enrolled_in_verified_course(self):
        """
        Test that if the user is not enrolled in verified track, then after sending the default no of
        emails, `expiry_email_date` is updated to None so that it's not filtered in the future for
        sending emails
        """
        user = UserFactory.create()
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.resend_days * (self.default_no_of_emails - 1))
        verification.expiry_email_date = today - timedelta(days=self.resend_days)
        verification.save()

        call_command('send_verification_expiry_email')

        # check that after sending the default number of emails, the expiry_email_date is set to none for a
        # user who is not enrolled in verified track
        attempt = SoftwareSecurePhotoVerification.objects.get(pk=verification.id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNone(attempt.expiry_email_date)

    def test_user_enrolled_in_verified_course(self):
        """
        Test that if the user is enrolled in verified track, then after sending the default no of
        emails, `expiry_email_date` is updated to now() so that it's filtered in the future to send
        email again
        """
        user = UserFactory.create()
        course = CourseFactory()
        CourseEnrollmentFactory.create(user=user, course_id=course.id, mode='verified')
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        verification = self.create_and_submit(user)
        verification.status = 'approved'
        verification.expiry_date = now() - timedelta(days=self.resend_days * (self.default_no_of_emails - 1))
        verification.expiry_email_date = today - timedelta(days=self.resend_days)
        verification.save()

        call_command('send_verification_expiry_email')

        attempt = SoftwareSecurePhotoVerification.objects.get(pk=verification.id)
        self.assertEqual(attempt.expiry_email_date, today)

    def test_number_of_emails_sent(self):
        """
        Tests that the number of emails sent in case the user is only enrolled in audit track are same
        as DEFAULT_EMAILS set in the settings
        """
        user = UserFactory.create()
        verification = self.create_and_submit(user)
        verification.status = 'approved'

        verification.expiry_date = now() - timedelta(days=1)
        verification.save()
        call_command('send_verification_expiry_email')

        # running the loop one extra time to verify that after sending DEFAULT_EMAILS no extra emails are sent and
        # for this reason expiry_email_date is set to None
        for i in range(1, self.default_no_of_emails + 2):
            if SoftwareSecurePhotoVerification.objects.get(pk=verification.id).expiry_email_date:
                today = now().replace(hour=0, minute=0, second=0, microsecond=0)
                verification.expiry_date = today - timedelta(days=self.resend_days * i + 1)
                verification.expiry_email_date = today - timedelta(days=self.resend_days)
                verification.save()
                call_command('send_verification_expiry_email')
            else:
                break

        # expiry_email_date set to None means it no longer will be filtered hence no emails will be sent in future
        self.assertIsNone(SoftwareSecurePhotoVerification.objects.get(pk=verification.id).expiry_email_date)
        self.assertEqual(len(mail.outbox), self.default_no_of_emails)
