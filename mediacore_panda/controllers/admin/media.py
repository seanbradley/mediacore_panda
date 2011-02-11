# This file is a part of MediaCore-Panda, Copyright 2011 Simple Station Inc.
#
# MediaCore is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MediaCore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
from pprint import pformat

from repoze.what.predicates import has_permission
from repoze.what.plugins.pylonshq import ActionProtector

from mediacore.controllers.admin.media import _get_new_file_json
from mediacore.lib.base import BaseController
from mediacore.lib.decorators import autocommit, expose
from mediacore.lib.filetypes import TRANSCODING
from mediacore.lib.storage import apply_meta_dict_to_media_file
from mediacore.model import MediaFile, fetch_row
from mediacore.model.meta import DBSession

from mediacore_panda.lib.storage import PandaStorage

log = logging.getLogger(__name__)
admin_perms = has_permission('edit')

class MediaController(BaseController):
    @ActionProtector(admin_perms)
    @expose('json')
    @autocommit
    def get_progress(self, file_id, **kwargs):
        media_file = fetch_row(MediaFile, file_id)

        # Get the encoding info for this particular file.
        if media_file.type == TRANSCODING:
            panda_client = media_file.storage.panda_helper().client

            # Update the file as needed
            encoding = panda_client.get_encoding(media_file.meta['panda_encoding_id'])
            self.update_media_file(media_file, encoding)
        else:
            encoding = None

        # The transcode has succeeded. It may have already succeeded in
        # the past, or its just happening now.
        if not encoding or encoding['status'] == 'success':
            return {
                'status': 'success',
                'data': _get_new_file_json(media_file,
                    include_status=True,
                    refresh_thumb=True,
                    include_size=True),
            }

        # The encoding is queued or actively transcoding.
        elif encoding['status'] == 'processing':
            if encoding['started_encoding_at']:
                return {'status': 'encoding', 'progress': encoding['encoding_progress']}
            else:
                return {'status': 'queued'}

        # An error has occurred and the encoding has failed.
        else:
            if 'error_class' in encoding:
                error = "%s: %s" % (encoding['error_class'],
                                    encoding.get('error_message'))
            else:
                error = 'An unexplained error occurred.'
            return {'status': 'fail', 'error': error}

    def update_media_file(self, encoding_file, encoding):
        log.debug('Encoding result:\n%s', pformat(encoding))

        if not encoding_file:
            log.info('Panda encoding does not correspond to a local file: panda_encoding_id %r',
                     encoding['id'])
            return

        if encoding['status'] == 'success':
            # FIXME: Support alternate path_formats
            encoding_file.unique_id = encoding['id'] + encoding['extname']

            encoding_file.type = u'video' # FIXME: Support audio formats
            encoding_file.width = encoding['width']
            encoding_file.height = encoding['height']
            encoding_file.size = encoding['file_size']

            bitrate = (encoding.get('audio_bitrate', None) or 0) \
                    + (encoding.get('video_bitrate', None) or 0)
            if bitrate:
                encoding_file.bitrate = bitrate

            meta = {
                'thumbnail_url': "%s%s_1.jpg" % (encoding_file.storage.base_urls[0][1], encoding['id']),
                'duration': int(round(encoding['duration'] / 1000.0)),
            }
            log.debug('Attempting to apply meta dict:\n%s', meta)
            apply_meta_dict_to_media_file(meta, encoding_file)

        elif encoding['status'] != 'processing':
            log.info('An encoding has failed: orig_file_id %r, video_id %r, encoding_id %r.',
                     encoding_file, encoding['video_id'], encoding['id'])

    @expose()
    @autocommit
    def panda_update(self, file_id=None, **kwargs):
        orig_file = DBSession.query(MediaFile).get(int(file_id))

        if not orig_file:
            log.debug('State update failure: file_id %r does not exist.', file_id)
            return ''

        panda_video_id = orig_file.meta['panda_video_id']
        encoding_files = {}

        # Collect the transcodings of this original file
        for file in orig_file.media.files:
            if file.id != orig_file.id \
            and isinstance(file.storage, PandaStorage) \
            and file.meta['panda_video_id'] == panda_video_id:
                panda_encoding_id = file.meta['panda_encoding_id']
                encoding_files[panda_encoding_id] = file

        if not encoding_files:
            log.debug('No encoding files found: orig_file_id %r, video_id %r', file_id, panda_video_id)
            return ''

        # Fetch updated encoding info from panda
        panda_storage = encoding_files.itervalues().next().storage
        panda_client = panda_storage.panda_helper().client
        panda_encodings = panda_client.get_encodings(video_id=panda_video_id)

        # Update each file according to its encoding info
        for encoding in panda_encodings:
            encoding_file = encoding_files.get(encoding['id'])
            self.update_media_file(encoding_file, encoding)
