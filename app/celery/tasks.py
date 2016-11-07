from datetime import (datetime)
from flask import current_app
from notifications_utils.recipients import (
    RecipientCSV
)
from notifications_utils.template import Template
from sqlalchemy.exc import SQLAlchemyError
from app import (
    create_uuid,
    DATETIME_FORMAT,
    notify_celery,
    encryption
)
from app.aws import s3
from app.celery import provider_tasks
from app.dao.jobs_dao import (
    dao_update_job,
    dao_get_job_by_id
)
from app.dao.notifications_dao import (dao_create_notification)
from app.dao.services_dao import dao_fetch_service_by_id, fetch_todays_total_message_count
from app.dao.templates_dao import dao_get_template_by_id
from app.models import (
    Notification,
    EMAIL_TYPE,
    SMS_TYPE,
    KEY_TYPE_NORMAL
)
from app.service.utils import service_allowed_to_send_to
from app.statsd_decorators import statsd


@notify_celery.task(name="process-job")
@statsd(namespace="tasks")
def process_job(job_id):
    start = datetime.utcnow()
    job = dao_get_job_by_id(job_id)

    if job.job_status != 'pending':
        return

    service = job.service

    total_sent = fetch_todays_total_message_count(service.id)

    if total_sent + job.notification_count > service.message_limit:
        job.job_status = 'sending limits exceeded'
        job.processing_finished = datetime.utcnow()
        dao_update_job(job)
        current_app.logger.info(
            "Job {} size {} error. Sending limits {} exceeded".format(
                job_id, job.notification_count, service.message_limit)
        )
        return

    job.job_status = 'in progress'
    dao_update_job(job)

    template = Template(
        dao_get_template_by_id(job.template_id, job.template_version).__dict__
    )

    for row_number, recipient, personalisation in RecipientCSV(
            s3.get_job_from_s3(str(service.id), str(job_id)),
            template_type=template.template_type,
            placeholders=template.placeholders
    ).enumerated_recipients_and_personalisation:

        encrypted = encryption.encrypt({
            'template': str(template.id),
            'template_version': job.template_version,
            'job': str(job.id),
            'to': recipient,
            'row_number': row_number,
            'personalisation': {
                key: personalisation.get(key)
                for key in template.placeholders
            }
        })

        if template.template_type == SMS_TYPE:
            send_sms.apply_async((
                str(job.service_id),
                create_uuid(),
                encrypted,
                datetime.utcnow().strftime(DATETIME_FORMAT)),
                queue='db-sms' if not service.research_mode else 'research-mode'
            )

        if template.template_type == EMAIL_TYPE:
            send_email.apply_async((
                str(job.service_id),
                create_uuid(),
                encrypted,
                datetime.utcnow().strftime(DATETIME_FORMAT)),
                queue='db-email' if not service.research_mode else 'research-mode'
            )

    finished = datetime.utcnow()
    job.job_status = 'finished'
    job.processing_started = start
    job.processing_finished = finished
    dao_update_job(job)
    current_app.logger.info(
        "Job {} created at {} started at {} finished at {}".format(job_id, job.created_at, start, finished)
    )


@notify_celery.task(bind=True, name="send-sms", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def send_sms(self,
             service_id,
             notification_id,
             encrypted_notification,
             created_at,
             api_key_id=None,
             key_type=KEY_TYPE_NORMAL):
    notification = encryption.decrypt(encrypted_notification)
    service = dao_fetch_service_by_id(service_id)

    if not service_allowed_to_send_to(notification['to'], service, key_type):
        current_app.logger.info(
            "SMS {} failed as restricted service".format(notification_id)
        )
        return

    try:
        dao_create_notification(
            Notification.from_api_request(
                created_at, notification, notification_id, service.id, SMS_TYPE, api_key_id, key_type
            )
        )
        provider_tasks.deliver_sms.apply_async(
            [notification_id],
            queue='send-sms' if not service.research_mode else 'research-mode'
        )

        current_app.logger.info(
            "SMS {} created at {} for job {}".format(notification_id, created_at, notification.get('job', None))
        )

    except SQLAlchemyError as e:
        current_app.logger.exception("RETRY: send_sms notification {}".format(notification_id), e)
        try:
            raise self.retry(queue="retry", exc=e)
        except self.MaxRetriesExceededError:
            current_app.logger.exception(
                "RETRY FAILED: task send_sms failed for notification {}".format(notification.id),
                e
            )


@notify_celery.task(bind=True, name="send-email", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def send_email(self, service_id,
               notification_id,
               encrypted_notification,
               created_at,
               api_key_id=None,
               key_type=KEY_TYPE_NORMAL):
    notification = encryption.decrypt(encrypted_notification)
    service = dao_fetch_service_by_id(service_id)

    if not service_allowed_to_send_to(notification['to'], service, key_type):
        current_app.logger.info("Email {} failed as restricted service".format(notification_id))
        return

    try:
        dao_create_notification(
            Notification.from_api_request(
                created_at, notification, notification_id, service.id, EMAIL_TYPE, api_key_id, key_type
            )
        )

        provider_tasks.deliver_email.apply_async(
            [notification_id],
            queue='send-email' if not service.research_mode else 'research-mode'
        )

        current_app.logger.info("Email {} created at {}".format(notification_id, created_at))
    except SQLAlchemyError as e:
        current_app.logger.exception("RETRY: send_email notification {}".format(notification_id), e)
        try:
            raise self.retry(queue="retry", exc=e)
        except self.MaxRetriesExceededError:
            current_app.logger.error(
                "RETRY FAILED: task send_email failed for notification {}".format(notification.id),
                e
            )
