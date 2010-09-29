from simplejson import dumps, loads

from mediacore.lib.storage import FileStorageEngine, LocalFileStorage, RemoteURLStorage, StorageURI, UnsuitableEngineError
from mediacore.lib.filetypes import guess_container_format, guess_media_type, VIDEO

from mycore.panda.lib import PANDA_URL_PREFIX, TYPES
from mycore.panda.forms.admin.storage import PandaForm
from mycore.panda.lib import PandaHelper

class PandaStorage(RemoteURLStorage, LocalFileStorage):

    engine_type = u'PandaStorage'
    """A uniquely identifying unicode string for the StorageEngine."""

    settings_form_class = PandaForm
    """Your :class:`mediacore.forms.Form` class for changing :attr:`_data`."""

    _default_data = {
        'access_key': u'',
        'secret_key': u'',
        'transcoding_enabled': False,
        'cloud_id': u'',
        'encoding_profiles': u'h264',
        'amazon_cloudfront_download_domain': u'',
        'amazon_cloudfront_streaming_domain': u'',
    }

    _panda_helper = None

    @property
    def base_urls(self):
        # TODO: need to init ph with proper credentials.
        return [
            ('http', 'http://s3.amazonaws.com/%s/' %
                self.panda_helper.client.get_cloud()['s3_videos_bucket']),

            ('http', 'http://%s/' %
                self._data['amazon_cloudfront_download_domain'].strip(' /')),

            ('rtmp', 'rtmp://%s/cfx/st/' %
                self._data['amazon_cloudfront_streaming_domain'].strip(' /')),
        ]

    @property
    def panda_helper(self):
        if self._panda_helper is None:
            # TODO: initialize this with prope credentials
            self._panda_helper = PandaHelper(
                cloud_id = self._data['cloud_id'],
                access_key = self._data['access_key'],
                secret_key = self._data['secret_key']
            )
        return self._panda_helper

    def parse(self, file=None, url=None):
        """Return metadata for the given file or raise an error.

        :type file: :class:`cgi.FieldStorage` or None
        :param file: A freshly uploaded file object.
        :type url: unicode or None
        :param url: A remote URL string.
        :rtype: dict
        :returns: Any extracted metadata.
        :raises UnsuitableEngineError: If file information cannot be parsed.

        """
        assert (file, url) != (None, None), "Must provide a file or a url."
        if not self._data['transcoding_enabled']:
            raise UnsuitableEngineError('Panda Transcoding with this Storage Engine is currently disabled.')

        if url and url.startswith(PANDA_URL_PREFIX):
            offset = len(PANDA_URL_PREFIX)
            # 'd' is the dict representing a Panda encoding or video
            # with an extra key: 'display_name'
            d = loads(url[offset:])

            # MediaCore uses extensions without prepended .
            ext = d['extname'].lstrip('.').lower()

            # XXX: Panda doesn't actually populate these fields yet.
            ba = d.get('audio_bitrate', None) or 0
            bv = d.get('video_bitrate', None) or 0
            bitrate = (ba + bv) or None

            return {
                'panda_id': d['id'],
                'panda_type': TYPES['video'],
                'panda_ext': ext,
                'container': guess_container_format(ext),
                'display_name': d['display_name'],
                'type': VIDEO, # only video files get panda encoded, so it's video Q.E.D.
                'height': d['height'],
                'width': d['width'],
                'size': d['file_size'],
                'bitrate': bitrate,
                'duration': d['duration'],
                'thumbnail_url': "%s%s.%s_thumb.jpg" % (self.base_urls[0][1], d['id'], ext),
            }
        elif url:
            return RemoteURLStorage.parse(self, url=url)
        elif file:
            return LocalFileStorage.parse(self, file=file)

    def store(self, media_file, file=None, url=None, meta=None):
        """Store the given file or URL and return a unique identifier for it.

        :type file: :class:`cgi.FieldStorage` or None
        :param file: A freshly uploaded file object.
        :type url: unicode or None
        :param url: A remote URL string.
        :type media_file: :class:`~mediacore.model.media.MediaFile`
        :param media_file: The associated media file object.
        :type meta: dict
        :param meta: The metadata returned by :meth:`parse`.
        :rtype: unicode or None
        :returns: The unique ID string. Return None if not generating it here.

        """
        assert (file, url) != (None, None), "Must provide a file or a url."
        assert media_file.id != None, "Media file must have an ID. Try flushing the DB Session."

        # Try to generate an ID based on the video_id or encoding_id
        # These IDs are available if the file is part of a transcoding job.
        # PandaStorage will handle these IDs directly
        if meta.get('panda_type', None) in (TYPES['video'], TYPES['encoding']):
            id = dict(
                type = meta['panda_type'],
                id = meta['panda_id'],
                ext = meta['panda_ext'],
            )
        else:
            # Otherwise, use the basic storage engines to handle this file.
            if file:
                # XXX: LocalFileStorage allows for the StorgeEngine to override the
                #      default file save directory. PandaStorage ignores this
                #      override, and is thus not necessarily compatible with the
                #      user's default LocalFileStorage instance.
                file_name = LocalFileStorage.store(self, media_file, file=file, meta=meta)
                id = dict(
                    type = TYPES['file'],
                    id = file_name,
                )
            elif url:
                # XXX: Notice that we don't call RemoteURLStorage.store() here.
                #      RemoteURLStorage sets the unique_id in the meta dict
                #      inside RemoteURLStorage.parse().
                id = dict(
                    type = TYPES['url'],
                    id = meta['unique_id'],
                )

            if meta['type'] == VIDEO:
                from mediacore.lib.helpers import url_for
                state_update_url = url_for(
                    controller='/panda/admin/media',
                    action='panda_update',
                    file_id=media_file.id,
                    qualified=True
                )
                profile_names = [x.strip() for x in self._data['encoding_profiles'].split(',')]
                profile_ids = self.panda_helper.profile_names_to_ids(profile_names)
                fake_ids(media_file, dumps(id), # FIXME: this is probably a BAD leaky abstraction.
                         self.panda_helper.transcode_media_file,
                         media_file, profile_ids, state_update_url=state_update_url)

        return dumps(id)

    def get_uris(self, media_file):
        """Return a list of URIs from which the stored file can be accessed.

        :type media_file: :class:`~mediacore.model.media.MediaFile`
        :param media_file: The associated media file object.
        :rtype: list
        :returns: All :class:`StorageURI` tuples for this file.

        """
        id = loads(media_file.unique_id)

        if id['type'] == TYPES['file']:
            return fake_ids(media_file, id['id'],
                            LocalFileStorage.get_uris, self, media_file)

        elif id['type'] == TYPES['url']:
            return fake_ids(media_file, id['id'],
                            RemoteURLStorage.get_uris, self, media_file)

        elif id['type'] in (TYPES['video'], TYPES['encoding']):
            return [
                StorageURI(media_file, base[0], "%s%s.%s" % (base[1], id['id'], id['ext']))
                for base in self.base_urls
            ]

FileStorageEngine.register(PandaStorage)

def fake_ids(mf, temp_id, func, *args, **kwargs):
    orig_id = mf.unique_id
    mf.unique_id = temp_id
    result = func(*args, **kwargs)
    mf.unique_id = orig_id
    return result
