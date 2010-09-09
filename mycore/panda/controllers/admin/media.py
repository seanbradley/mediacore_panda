import logging

from repoze.what.predicates import has_permission
from repoze.what.plugins.pylonshq import ActionProtector

from mediacore.lib.base import BaseController
from mediacore.lib.decorators import expose
from mediacore.lib.helpers import redirect
from mediacore.model import Media, MediaFile, fetch_row
from mycore.panda.lib import PandaHelper

log = logging.getLogger(__name__)
admin_perms = has_permission('admin')

class MediaController(BaseController):
    @ActionProtector(admin_perms)
    @expose('panda/admin/media/panda-status-box.html')
    def panda_status(self, id, **kwargs):
        media = fetch_row(Media, id)
        ph = PandaHelper()
        return dict(
            media = media,
            encoding_dicts = ph.get_all_associated_encoding_dicts(media.files),
            video_dicts = ph.get_all_associated_video_dicts(media.files),
            profile_names = ph.get_profile_ids_names(),
            include_javascript = False,
        )

    @ActionProtector(admin_perms)
    @expose('json')
    def panda_cancel(self, file_id, encoding_id, **kwargs):
        media_file = fetch_row(MediaFile, file_id)
        ph = PandaHelper()
        ph.cancel_transcode(media_file, encoding_id)
        return dict(
            success = True,
        )

    @ActionProtector(admin_perms)
    @expose('json')
    def panda_retry(self, file_id, encoding_id, **kwargs):
        media_file = fetch_row(MediaFile, file_id)
        ph = PandaHelper()
        ph.retry_transcode(media_file, encoding_id)
        return dict(
            success = True,
        )

    @expose()
    def panda_update(self, media_id=None, file_id=None, video_id=None):
        if file_id:
            media_file = fetch_row(MediaFile, file_id)
            media_files = [media_file]
        elif media_id:
            media = fetch_row(Media, media_id)
            media_files = media.files

        ph = PandaHelper()
        for media_file in media_files:
            ph.video_status_update(media_file, video_id)

        redirect(controller='/admin/media', action='edit', id=media_id)
