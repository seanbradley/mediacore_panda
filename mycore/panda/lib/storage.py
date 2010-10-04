import simplejson

from mediacore.lib.decorators import memoize
from mediacore.lib.helpers import url_for
from mediacore.lib.storage import FileStorageEngine, LocalFileStorage, StorageURI, UnsuitableEngineError
from mediacore.lib.filetypes import guess_container_format, guess_media_type, VIDEO

from mycore.panda.lib import PANDA_URL_PREFIX, TYPES
from mycore.panda.forms.admin.storage import PandaForm
from mycore.panda.lib import PandaHelper

class PandaStorage(FileStorageEngine):

    engine_type = u'PandaStorage'
    """A uniquely identifying unicode string for the StorageEngine."""

    default_name = u'Panda Transcoding & Storage'

    settings_form_class = PandaForm
    """Your :class:`mediacore.forms.Form` class for changing :attr:`_data`."""

    second_to = []

    _default_data = {
        'access_key': u'',
        'secret_key': u'',
        'transcoding_enabled': False,
        'cloud_id': u'',
        'encoding_profiles': u'h264',
        'amazon_cloudfront_download_domain': u'',
        'amazon_cloudfront_streaming_domain': u'',
    }

    @property
    @memoize
    def base_urls(self):
        s3_bucket = self.panda_helper.client.get_cloud()['s3_videos_bucket']
        cloudfront_http = self._data['amazon_cloudfront_download_domain']
        cloudfront_rtmp = self._data['amazon_cloudfront_streaming_domain']
        # TODO: Return a dict or something easier to parse elsewhere
        urls = [('http', 'http://%s.s3.amazonaws.com/' % s3_bucket)]
        if cloudfront_http:
            urls.append(('http', 'http://%s/' % cloudfront_http.strip(' /')))
        else:
            urls.append((None, None))
        if cloudfront_rtmp:
            urls.append(('rtmp', 'rtmp://%s/cfx/st/' % cloudfront_rtmp.strip(' /')))
        else:
            urls.append((None, None))
        return urls

    @property
    @memoize
    def panda_helper(self):
        return PandaHelper(
            cloud_id = self._data['cloud_id'],
            access_key = self._data['access_key'],
            secret_key = self._data['secret_key'],
        )

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

        if not url or not url.startswith(PANDA_URL_PREFIX):
            raise UnsuitableEngineError()

        offset = len(PANDA_URL_PREFIX)
        # 'd' is the dict representing a Panda encoding or video
        # with an extra key: 'display_name'
        d = simplejson.loads(url[offset:])

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
            'thumbnail_url': "%s%s_1.jpg" % (self.base_urls[0][1], d['id']),
        }

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
        assert media_file.id != None, "Media file must have an ID. Try flushing the DB Session."

        # Generate an ID based on the video_id or encoding_id
        id = dict(
            type = meta['panda_type'],
            id = meta['panda_id'],
            ext = meta['panda_ext'],
        )

        return simplejson.dumps(id)

    def transcode(self, media_file):
        if isinstance(media_file.storage, PandaStorage):
            return
        state_update_url = url_for(
            controller='/panda/admin/media',
            action='panda_update',
            file_id=media_file.id,
            qualified=True
        )
        profile_names = [x.strip() for x in self._data['encoding_profiles'].split(',')]
        profile_ids = self.panda_helper.profile_names_to_ids(profile_names)
        # XXX: This method may fail if there is no 'http' uri available
        #      for the given media_file
        self.panda_helper.transcode_media_file(media_file, profile_ids, state_update_url=state_update_url)

    def get_uris(self, media_file):
        """Return a list of URIs from which the stored file can be accessed.

        :type media_file: :class:`~mediacore.model.media.MediaFile`
        :param media_file: The associated media file object.
        :rtype: list
        :returns: All :class:`StorageURI` tuples for this file.

        """
        id = simplejson.loads(media_file.unique_id)
        base_urls = list(self.base_urls)

        # Skip s3 http url if cloudfront http url is available
        if base_urls[1][0]:
            base_urls = base_urls[1:]

        uris = []
        for scheme, base_url in base_urls:
            if not scheme:
                continue
            file_uri = '%s.%s' % (id['id'], id['ext'])
            if scheme == 'rtmp':
                uri = StorageURI(media_file, scheme, file_uri, base_url)
            else:
                uri = StorageURI(media_file, scheme, base_url + file_uri)
            uris.append(uri)
        return uris

FileStorageEngine.register(PandaStorage)
# MonkeyPatch LocalFileStorage to depend on this
LocalFileStorage.second_to += [PandaStorage]
