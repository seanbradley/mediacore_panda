import os
import urllib
import cPickle
import panda

from socket import gaierror
from paste.deploy.converters import asbool
from simplejson import dumps, loads

from mediacore.lib.helpers import merge_dicts
from mediacore.lib.compat import all
from mediacore.lib.storage import add_new_media_file, UnsuitableEngineError, StorageURI

import logging
log = logging.getLogger(__name__)

# Monkeypatch panda.urlescape as per http://github.com/newbamboo/panda_client_python/commit/43e9d613bfe34ae09f2815bf026e5a5f5f0abd0a
def urlescape(s):
    s = unicode(s)
    return urllib.quote(s).replace("%7E", "~").replace(' ', '%20').replace('/', '%2F')
panda.urlescape = urlescape

PUT = 'PUT'
POST = 'POST'
DELETE = 'DELETE'
GET = 'GET'

META_VIDEO_PREFIX = "panda_video_"
PANDA_URL_PREFIX = "panda:"
TYPES = {
    'video': "video_id",
    'encoding': "encoding_id",
    'file': "file_name",
    'url': "url",
}

# TODO: Use these lists to verify that all received data has a valid structure.
cloud_keys = [
    'id', 'created_at', 'updated_at', # Common
    'name', 's3_private_access', 's3_videos_bucket', # Cloud-specific
]
profile_keys = [
    'id', 'extname', 'created_at', 'updated_at', 'height', 'width', # Common
    'title', 'name', 'preset_name', # Profile-specific
]
video_keys = [
    'id', 'extname', 'created_at', 'updated_at', 'height', 'width', # Common
    'file_size', 'status', # Video/Encoding specific
    'source_url', 'original_filename', 'audio_codec', 'video_codec', 'duration', 'fps', # Video Specific
]
encoding_keys = [
    'id', 'extname', 'created_at', 'updated_at', 'height', 'width', # Common
    'file_size', 'status', # Video/Encoding Specific
    'encoding_progress', 'encoding_time', 'started_encoding_at', 'profile_id', 'video_id', # Encoding Specific
]

class PandaException(Exception):
    pass

def log_request(request_url, method, query_string_data, body_data, response_data):
    from pylons import request
    log.debug("MediaCore, from: %s", request.url)
    log.debug("Sent Panda a %s request: %s", method, request_url)
    log.debug("Query String Data: %r", query_string_data)
    log.debug("Request Body Data: %r", body_data)
    log.debug("Received response: %r", response_data)

from pylons.i18n import N_ as _
from mediacore.forms import ListFieldSet, ResetButton, SubmitButton, TextField
from mediacore.forms.admin.storage import StorageForm
from mediacore.forms.admin.settings import real_boolean_radiobuttonlist as boolean_radiobuttonlist

class PandaForm(StorageForm):
    template = 'mycore.panda.templates.admin.storage'
    fields = StorageForm.fields + [
        boolean_radiobuttonlist('transcoding_enabled', label_text=_('Automatically transcode uploaded videos using Panda')),
        ListFieldSet('panda', suppress_label=True, legend=_('Panda Account Details:'), css_classes=['details_fieldset'], children=[
            TextField('cloud_id', maxlength=255, label_text=_('Cloud ID')),
            TextField('access_key', maxlength=255, label_text=_('Access Key')),
            TextField('secret_key', maxlength=255, label_text=_('Secret Key')),
        ]),
        TextField('encoding_profiles', label_text=_('Encodings to use (comma-separated list of encoding names)')),
        ListFieldSet('amazon', suppress_label=True, legend=_('Amazon CloudFront Domains (e.g. a1b2c3d4e5f6.cloudfront.net):'), css_classes=['details_fieldset'], children=[
            TextField('amazon_cloudfront_download_domain', maxlength=255, label_text=_('CloudFront HTTP')),
            TextField('amazon_cloudfront_streaming_domain', maxlength=255, label_text=_('CloudFront RTMP')),
        ]),
    ] + StorageForm.buttons

    def display(self, value, engine, **kwargs):
        kwargs['engine'] = engine
        try:
            kwargs['profiles'] = engine.panda_helper.client.get_profiles()
            kwargs['cloud'] = engine.panda_helper.client.get_cloud()
        except PandaException, e:
            kwargs['profiles'] = None
            kwargs['cloud'] = None

        merge_dicts(value, self._nest_values_for_form(engine._data))

        # kwargs are vars for the template, value is a dict of values for the form.
        return StorageForm.display(self, value, **kwargs)

    def save_engine_params(self, engine, **kwargs):
        """Map validated field values to engine data.

        Since form widgets may be nested or named differently than the keys
        in the :attr:`mediacore.lib.storage.StorageEngine._data` dict, it is
        necessary to manually map field values to the data dictionary.

        :type engine: :class:`mediacore.lib.storage.StorageEngine` subclass
        :param engine: An instance of the storage engine implementation.
        :param \*\*kwargs: Validated and filtered form values.
        :raises formencode.Invalid: If some post-validation error is detected
            in the user input. This will trigger the same error handling
            behaviour as with the @validate decorator.

        """
        data = self._flatten_values_from_form(engine._data, kwargs)
        if data['transcoding_enabled']:
            # Only set transcoding_enabled to True if the user has selected it
            # AND the specified data works for connecting Panda's servers.
            data['transcoding_enabled'] = False
            if all((data[k] for k in ['cloud_id', 'access_key', 'secret_key'])):
                try:
                    # Attempt to connect...
                    ph = PandaHelper(
                        cloud_id = data['cloud_id'],
                        access_key = data['access_key'],
                        secret_key = data['secret_key']
                    )
                    ph.client.get_cloud()
                    # If we got to this point, the account works.
                    data['transcoding_enabled'] = True
                except PandaException, e:
                    pass
        engine._data = data

class PandaClient(object):
    def __init__(self, cloud_id, access_key, secret_key):
        self.conn = panda.Panda(cloud_id, access_key, secret_key)
        self.json_cache = {}

    def _get_json(self, url, query_string_data={}):
        # This function is memoized with a custom hashing algorithm for its arguments.
        hash_tuple = url, ((k, query_string_data[k]) for k in sorted(query_string_data.keys()))
        if hash_tuple in self.json_cache:
            return self.json_cache[hash_tuple]

        try:
            json = self.conn.get(request_path=url, params=query_string_data)
        except gaierror, e:
            # Catch socket errors and re-raise them as Panda errors.
            raise PandaException(e)

        obj = loads(json)
        log_request(url, GET, query_string_data, None, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])

        self.json_cache[hash_tuple] = obj
        return obj

    def _post_json(self, url, post_data={}):
        json = self.conn.post(request_path=url, params=post_data)
        obj = loads(json)
        log_request(url, POST, None, post_data, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        return obj

    def _put_json(self, url, put_data={}):
        json = self.conn.put(request_path=url, params=put_data)
        obj = loads(json)
        log_request(url, PUT, None, put_data, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        return obj

    def _delete_json(self, url, query_string_data={}):
        json = self.conn.delete(request_path=url, params=query_string_data)
        obj = loads(json)
        log_request(url, DELETE, query_string_data, None, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        return obj

    def get_cloud(self):
        """Get the data for the currently selected Panda cloud."""
        url = '/clouds/%s.json' % self.conn.cloud_id
        return self._get_json(url)

    def get_presets(self):
        """Get the configuration options for the existing encoding presets in this cloud."""
        url = '/presets.json'
        return self._get_json(url)

    def get_videos(self, status=None):
        """List all videos, filtered by status.

        :param status: Filter by status. One of 'success', 'fail', 'processing'.
        :type status: str

        :rtype: list of dicts
        """
        data = {}
        if status in ('success', 'fail', 'processing'):
            data['status'] = status
        return self._get_json('/videos.json', data)

    def get_encodings(self, status=None, profile_id=None, profile_name=None, video_id=None):
        """List all encoded instances of all videos, filtered by whatever critera are provided.

        :param status: Filter by status. One of 'success', 'fail', 'processing'.
        :type status: str

        :param profile_id: filter by profile_id
        :type profile_id: str

        :param profile_name: filter by profile_name
        :type profile_name: str

        :param video_id: filter by video_id
        :type video_id: str

        :rtype: list of dicts
        """
        data = {}
        if status in ('success', 'fail', 'processing'):
            data['status'] = status
        if profile_id:
            data['profile_id'] = profile_id
        if profile_name:
            data['profile_name'] = profile_name
        if video_id:
            data['video_id'] = video_id
        return self._get_json('/encodings.json', data)

    def get_profiles(self):
        """List all encoding profiles.

        :rtype: list of dicts
        """
        return self._get_json('/profiles.json')

    def get_video(self, video_id):
        """Get the details for a single video.

        :param video_id: The ID string of the video.
        :type video_id: str

        :rtype: dict
        """
        url = '/videos/%s.json' % video_id
        return self._get_json(url)

    def get_encoding(self, encoding_id):
        """Get the details for a single encoding of a video.

        :param encoding_id: The ID string of the encoding instance.
        :type encoding_id: str

        :rtype: dict
        """
        url = '/encodings/%s.json' % encoding_id
        return self._get_json(url)

    def get_profile(self, profile_id):
        """Get the details for a single encoding profile.

        :param profile_id: The ID string of the profile.
        :type profile_id: str

        :rtype: dict
        """
        url = '/profiles/%s.json' % profile_id
        return self._get_json(url)

    def add_profile(self, title, extname, width, height, command, name=None):
        """Add a profile using the settings provided.

        :param title: Human-readable name (e.g. "MP4 (H.264) Hi")
        :type title: str

        :param name: Machine-readable name (e.g. "h264.hi")
        :type name: str

        :param extname: file extension (including preceding .)
        :type extname: str

        :param width: Width of the encoded video
        :type width: int

        :param height: Height of the encoded video
        :type height: int

        :param command: The command to run the transcoding job.
                        (e.g. "ffmpeg -i $input_file$ -acodec libfaac -ab 128k -vcodec libx264 -vpre normal $resolution_and_padding$ -y $output_file$")
                        See http://www.pandastream.com/docs/encoding_profiles
        :type command: str
        """
        data = dict(
            title = title,
            extname = extname,
            width = width,
            height = height,
            command = command,
            name = name
        )
        if not name:
            data.pop('name')
        return self._post_json('/profiles.json', data)

    def add_profile_from_preset(self, preset_name, name=None, width=None, height=None):
        """Add a profile based on the provided preset, extending with the settings provided.

        :param preset_name: The name of the preset that will provide the basis for this encoding.
        :type preset_name: str

        :param name: Machine-readable name (e.g. "h264.hi")
        :type name: str

        :param width: Width of the encoded video
        :type width: int

        :param height: Height of the encoded video
        :type height: int
        """
        data = dict(
            preset_name = preset_name,
            name = name,
            width = width,
            height = height
        )
        for x in data:
            if data[x] == None:
                data.pop(x)
        return self._post_json('/profiles.json', data)

    def delete_encoding(self, encoding_id):
        """Delete the reference to a particular encoding from the Panda servers.

        :param encoding_id: The ID string of the encoding instance.
        :type encoding_id: str

        :returns: boolean success
        :rtype: True or False
        """
        url = '/encodings/%s.json' % encoding_id
        return self._delete_json(url)['deleted']

    def delete_video(self, video_id):
        """Delete the reference to a particular video from the Panda servers.

        :param video_id: The ID string of the video.
        :type video_id: str

        :returns: boolean success
        :rtype: True or False
        """
        url = '/videos/%s.json' % video_id
        return self._delete_json(url)['deleted']

    def delete_profile(self, profile_id):
        """Delete a particular profile from the Panda servers.

        :param profile_id: The ID string of the profile.
        :type profile_id: str

        :returns: boolean success
        :rtype: True or False
        """
        url = '/profiles/%s.json' % profile_id
        return self._delete_json(url)['deleted']

    def transcode_file(self, file_or_source_url, profile_ids, state_update_url=None):
        """Upload or mark a video file for transcoding.

        :param file_or_source_url: A file object or url to transfer to Panda
        :type file_or_source_url: A file-like object or str

        :param profile_ids: List of profile IDs to encode the video with.
        :type profile_ids: list of str

        :param state_update_url: URL for Panda to send a notification to when
                                 encoding is complete. See docs for details
                                 http://www.pandastream.com/docs/api
        :type state_update_url: str

        :returns: a dict representing the newly created video object
        :rtype: dict
        """
        if not profile_ids:
            raise Exception('Must provide at least one profile ID.')

        if not isinstance(file_or_source_url, basestring):
            raise Exception('File-like objects are not currently supported.')

        data = {
            'source_url': file_or_source_url,
            'profiles': ','.join(profile_ids),
        }
        if state_update_url:
            data['state_update_url'] = state_update_url
        return self._post_json('/videos.json', data)

    def add_transcode_profile(self, video_id, profile_id):
        """Add a transcode profile to an existing Panda video.

        :param video_id: The ID string of the video.
        :type video_id: str

        :param profile_id: The ID string of the profile.
        :type profile_id: str

        :returns: a dict representing the newly created encoding object
        :rtype: dict
        """
        data = {
            'video_id': video_id,
            'profile_id': profile_id,
        }
        return self._post_json('/encodings.json', data)


class PandaHelper(object):
    def __init__(self, cloud_id, access_key, secret_key):
        self.client = PandaClient(cloud_id, access_key, secret_key)

    def profile_names_to_ids(self, names):
        profiles = self.client.get_profiles()
        ids = []
        for p in profiles:
            if p['name'] in names:
                ids.append(p['id'])
        return ids

    def profile_ids_to_names(self, ids):
        profiles = self.client.get_profiles()
        names = []
        for p in profiles:
            if p['id'] in ids and p['name'] not in names:
                names.append(p['name'])
        return names

    def get_profile_ids_names(self):
        profiles = self.client.get_profiles()
        out = {}
        for profile in profiles:
            out[profile['id']] = profile['name']
        return out

    def associate_video_id(self, media_file, video_id, state=None):
        # Create a meta_key for this MediaCore::MediaFile -> Panda::Video pairing.
        # This is sort of a perversion of the meta table, but hey, it works.
        meta_key = "%s%s" % (META_VIDEO_PREFIX, video_id)
        media_file.meta[meta_key] = state

    def disassociate_video_id(self, media_file, video_id):
        from mediacore.model import DBSession
        from mediacore.model.media import MediaFilesMeta
        # Create a meta_key for this MediaCore::MediaFile -> Panda::Video pairing.
        # This is sort of a perversion of the meta table, but hey, it works.
        meta_key = "%s%s" % (META_VIDEO_PREFIX, video_id)
        mfm = DBSession.query(MediaFilesMeta)\
                .filter(MediaFilesMeta.media_files_id==media_file.id)\
                .filter(MediaFilesMeta.key==meta_key)
        for x in mfm:
            DBSession.delete(x)

    def list_associated_video_ids(self, media_file):
        # This method returns a list, for futureproofing and testing, but the
        # current logic basically ensures that the list will have at most one element.
        ids = []
        offset = len(META_VIDEO_PREFIX)
        for key, value in media_file.meta.iteritems():
            if key.startswith(META_VIDEO_PREFIX):
                ids.append(key[offset:])
        return ids

    def get_associated_video_dicts(self, media_file):
        ids = self.list_associated_video_ids(media_file)
        video_dicts = {}
        for id in ids:
            video = self.client.get_video(id)
            video_dicts[video['id']] = video
        return video_dicts

    def get_associated_encoding_dicts(self, media_file):
        ids = self.list_associated_video_ids(media_file)
        encoding_dicts = {}
        for id in ids:
            v_encodings = self.client.get_encodings(video_id=id)
            for encoding in v_encodings:
                encoding_dicts[encoding['id']] = encoding
        return encoding_dicts

    def get_all_associated_encoding_dicts(self, media_files):
        encoding_dicts = {}
        for file in media_files:
            dicts = self.get_associated_encoding_dicts(file)
            if dicts:
                encoding_dicts[file.id] = dicts
        return encoding_dicts

    def get_all_associated_video_dicts(self, media_files):
        video_dicts = {}
        for file in media_files:
            dicts = self.get_associated_video_dicts(file)
            if dicts:
                video_dicts[file.id] = dicts
        return video_dicts

    def cancel_transcode(self, media_file, encoding_id):
        video_ids = self.list_associated_video_ids(media_file)

        # Ensure that the encoding to retry belongs to the given media file.
        e = self.client.get_encoding(encoding_id)
        if e['video_id'] not in video_ids:
            raise PandaException('Specified encoding is not associated with the specified media file. Cannot cancel job.', encoding_id, media_file)
        self.client.delete_video(e['video_id'])
        self.disassociate_video_id(media_file, e['video_id'])

    def retry_transcode(self, media_file, encoding_id):
        # Ensure that the encoding to retry belongs to the given media file.
        e = self.client.get_encoding(encoding_id)
        video_ids = self.list_associated_video_ids(media_file)
        if e['video_id'] not in video_ids:
            raise PandaException('Specified encoding is not associated with the specified media file. Cannot retry.', encoding_id, media_file)

        # Upon successful deletion of the old encoding object, retry!
        if self.client.delete_encoding(encoding_id):
            self.client.add_transcode_profile(e['video_id'], e['profile_id'])
        else:
            raise PandaException('Could not delete specified encoding.', encoding_id)

    def transcode_media_file(self, media_file, profile_ids, state_update_url=None):
        uris = [u.file_uri for u in media_file.get_uris() if u.scheme == 'http']
        download_url = uris[0]
        transcode_details = self.client.transcode_file(download_url, profile_ids, state_update_url)
        self.associate_video_id(media_file, transcode_details['id'])

    def video_status_update(self, media_file, video_id=None):
        # If no ID is specified, update all associated videos!
        if video_id is None:
            video_ids = self.list_associated_video_ids(media_file)
            for video_id in video_ids:
                self.video_status_update(media_file, video_id)
            return

        v = self.client.get_video(video_id)
        encodings = self.client.get_encodings(video_id=video_id)

        # Only proceed if the video has completed all encoding steps successfully.
        if any(e['status'] != 'success' for e in encodings):
            return

        # Set the media's duration based on the video file.
        if v['duration'] and not media_file.media.duration:
            media_file.media.duration = v['duration']/1000

        profiles = self.get_profile_ids_names()

        # For each successful encoding (and the original file), create a new MediaFile
        v['display_name'] = "(%s) %s%s" % ('original', media_file.display_name, v['extname'])
        url = PANDA_URL_PREFIX + dumps(v)
        new_mf = add_new_media_file(media_file.media, url=url)
        for e in encodings:
            # Panda reports multi-bitrate http streaming encodings as .ts file
            # but the associated playlist is the only thing ipods, etc, can read.
            if e['extname'] == '.ts':
                e['extname'] = '.m3u8'

            e['display_name'] = "(%s) %s%s" % (profiles[e['profile_id']], media_file.display_name, e['extname'])
            url = PANDA_URL_PREFIX + dumps(e)
            new_mf = add_new_media_file(media_file.media, url=url)

        self.disassociate_video_id(media_file, v['id'])
        # TODO: Now delete the exisitng media_file?

from mediacore.lib.storage import FileStorageEngine, LocalFileStorage, RemoteURLStorage
from mediacore.lib.filetypes import guess_container_format, guess_media_type, VIDEO

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
