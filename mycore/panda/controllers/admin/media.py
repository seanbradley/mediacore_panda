import logging

from repoze.what.predicates import has_permission
from repoze.what.plugins.pylonshq import ActionProtector

from mediacore.lib.base import BaseController
from mediacore.lib.decorators import expose
from mediacore.lib.helpers import redirect
from mediacore.model import Media, MediaFile, fetch_row
from mycore.panda.lib import PandaHelper
from mycore.panda import add_panda_vars

log = logging.getLogger(__name__)
admin_perms = has_permission('admin')

class MediaController(BaseController):
    @ActionProtector(admin_perms)
    @expose('panda/admin/media/panda-status-box.html')
    def panda_status(self, id, **kwargs):
        media = fetch_row(Media, id)
        result = {'media': media, 'include_javascript': False}
        return add_panda_vars(**result)

    @ActionProtector(admin_perms)
    @expose('json')
    def panda_cancel(self, file_id, encoding_id, **kwargs):
        media_file = fetch_row(MediaFile, file_id)
        media_file.storage.panda_helper.cancel_transcode(media_file, encoding_id)
        return dict(
            success = True,
        )

    @ActionProtector(admin_perms)
    @expose('json')
    def panda_retry(self, file_id, encoding_id, **kwargs):
        media_file = fetch_row(MediaFile, file_id)
        media_file.storage.panda_helper.retry_transcode(media_file, encoding_id)
        return dict(
            success = True,
        )

    @expose()
    def panda_update(self, media_id=None, file_id=None, video_id=None, **kwargs):
        if file_id:
            media_file = fetch_row(MediaFile, file_id)
            media_files = [media_file]
        elif media_id:
            media = fetch_row(Media, media_id)
            media_files = media.files

        for media_file in media_files:
            media_file.storage.panda_helper.video_status_update(media_file, video_id)

        redirect(controller='/admin/media', action='edit', id=media_id)
