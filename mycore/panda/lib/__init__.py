import os
import urllib
import cPickle
import panda

from paste.deploy.converters import asbool
from simplejson import dumps, loads

from mediacore.lib.compat import all

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

ENCODING_COMPLETE = 'encoding_complete'

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
    'error_log', # Unofficial attribute, added by PandaClient
]

def s3_base_urls():
    """Return fully qualified (including protocol) URIs for the base path of all specified Amazon distribution options."""
    from pylons import app_globals
    ph = PandaHelper()
    s3_url_http = 'http://s3.amazonaws.com/%s/' % ph.client.get_cloud()['s3_videos_bucket']
    cf_url_http = 'http://%s/' % app_globals.settings['panda_amazon_cloudfront_download_domain'].strip(' /')
    cf_url_rtmp = 'rtmp://%s/cfx/st/' % app_globals.settings['panda_amazon_cloudfront_streaming_domain'].strip(' /')
    urls = [
        (s3_url_http, 'S3 - HTTP'),
        (cf_url_http, 'CF - HTTP'),
        (cf_url_rtmp, 'CF - RTMP'),
    ]
    min_length = len('xxxp:///')
    # only return the cloudfront urls if they have some content
    return [(x, y) for x, y in urls if len(x) > min_length]

class memoize(object):
    def __init__(self, fn):
        self.fn = fn
        self.cache = {}
        doc = "This method is memoized. Use the update_cache=True kwarg to avoid stale data."
        if fn.__doc__:
            self.__doc__ = "\n".join(fn.__doc__, doc)
        else:
            self.__doc__ = doc

    def __call__(self, *args, **kwargs):
        update_cache = kwargs.pop('update_cache', False)
        key = cPickle.dumps((args, sorted(kwargs.iteritems())))
        if update_cache or key not in self.cache:
            self.cache[key] = self.fn(*args, **kwargs)
        return self.cache[key]

    def clear_cache(self):
        self.cache = {}

class PandaException(Exception):
    pass

def log_request(request_url, method, query_string_data, body_data, response_data):
    from pylons import request
    log.debug("MediaCore, from: %s", request.url)
    log.debug("Sent Panda a %s request: %s", method, request_url)
    log.debug("Query String Data: %r", query_string_data)
    log.debug("Request Body Data: %r", body_data)
    log.debug("Received response: %r", response_data)

class PandaClient(object):
    def __init__(self, cloud_id, access_key, secret_key):
        if not all((cloud_id, access_key, secret_key)):
            raise PandaException('Cannot initialize PandaClient object if any arguments are None')
        self.conn = panda.Panda(cloud_id, access_key, secret_key)

        # Avoid making a whole bunch of identical requests.
        self._get_json = memoize(self._get_json)

    def _add_extra_fields(self, url, method, obj):
        """Add extra fields to every dict that we generate.

        For instance, encoding dicts get an 'error_log' url associated, here.
        """
        if isinstance(obj, list):
            for o in obj:
                self._add_extra_fields(url, method, o)
            return

        if url.startswith('/encoding') and method in (GET, POST):
            # If there was an error log for this encoding, it would be at this URL
            obj['error_log'] = "%s%s.log" % (s3_base_urls()[0][0], obj['id'])

    def _get_json(self, url, query_string_data={}):
        json = self.conn.get(request_path=url, params=query_string_data)
        obj = loads(json)
        log_request(url, GET, query_string_data, None, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        self._add_extra_fields(url, GET, obj)
        return obj

    def _post_json(self, url, post_data={}):
        json = self.conn.post(request_path=url, params=post_data)
        obj = loads(json)
        log_request(url, POST, None, post_data, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        self._add_extra_fields(url, POST, obj)
        return obj

    def _put_json(self, url, put_data={}):
        json = self.conn.put(request_path=url, params=put_data)
        obj = loads(json)
        log_request(url, PUT, None, put_data, obj)
        if 'error' in obj:
            raise PandaException(obj['error'], obj['message'])
        self._add_extra_fields(url, PUT, obj)
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


_meta_video_prefix = "panda_video_"
_meta_encoding_prefix = "panda_encoding_"

class PandaHelper(object):
    def __init__(self, cloud_id=None, access_key=None, secret_key=None, ignore_enabled=False):
        from pylons import app_globals

        if not ignore_enabled:
            if not asbool(app_globals.settings['panda_transcoding_enabled']):
                raise PandaException('Panda transcoding is not enabled in settings. Use ignore_enabled kwarg to bypass this restriction.')

        self.client = PandaClient(
            cloud_id or app_globals.settings['panda_cloud_id'],
            access_key or app_globals.settings['panda_access_key'],
            secret_key or app_globals.settings['panda_secret_key'],
        )

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

    def associate_encoding_id(self, media_file, encoding_id, state=None):
        # Create a meta_key for this MediaCore::MediaFile -> Panda::Encoding pairing.
        # This is sort of a perversion of the meta table, but hey, it works.
        meta_key = "%s%s" % (_meta_encoding_prefix, encoding_id)
        media_file.meta[meta_key] = state

    def associate_video_id(self, media_file, video_id, state=None):
        # Create a meta_key for this MediaCore::MediaFile -> Panda::Video pairing.
        # This is sort of a perversion of the meta table, but hey, it works.
        meta_key = "%s%s" % (_meta_video_prefix, video_id)
        media_file.meta[meta_key] = state

    def disassociate_video_id(self, media_file, video_id):
        from mediacore.model import DBSession
        from mediacore.model.media import MediaFilesMeta
        # Create a meta_key for this MediaCore::MediaFile -> Panda::Video pairing.
        # This is sort of a perversion of the meta table, but hey, it works.
        meta_key = "%s%s" % (_meta_video_prefix, video_id)
        mfm = DBSession.query(MediaFilesMeta)\
                .filter(MediaFilesMeta.media_files_id==media_file.id)\
                .filter(MediaFilesMeta.key==meta_key)
        for x in mfm:
            DBSession.delete(x)

    def list_associated_video_ids(self, media_file, include_completed=False):
        # This method returns a list, for futureproofing and testing, but the
        # current logic basically ensures that the list will have at most one element.
        ids = []
        offset = len(_meta_video_prefix)
        for key, value in media_file.meta.iteritems():
            if key.startswith(_meta_video_prefix) \
            and (include_completed or value != ENCODING_COMPLETE):
                ids.append(key[offset:])
        return ids

    def list_associated_encoding_ids(self, media_file, include_completed=False):
        # This method returns a list, for futureproofing and testing, but the
        # current logic basically ensures that the list will have at most one element.
        ids = []
        offset = len(_meta_encoding_prefix)
        for key, value in media_file.meta.iteritems():
            if key.startswith(_meta_encoding_prefix) \
            and (include_completed or value != ENCODING_COMPLETE):
                ids.append(key[offset:])
        return ids

    def get_associated_video_dicts(self, media_file, include_completed=False):
        ids = self.list_associated_video_ids(media_file, include_completed)
        video_dicts = {}
        for id in ids:
            video = self.client.get_video(id)
            video_dicts[video['id']] = video
        return video_dicts

    def get_associated_encoding_dicts(self, media_file, include_completed=False):
        ids = self.list_associated_video_ids(media_file, include_completed)
        encoding_dicts = {}
        for id in ids:
            v_encodings = self.client.get_encodings(video_id=id)
            for encoding in v_encodings:
                encoding_dicts[encoding['id']] = encoding
        return encoding_dicts

    def get_all_associated_encoding_dicts(self, media_files, include_completed=False):
        encoding_dicts = {}
        for file in media_files:
            dicts = self.get_associated_encoding_dicts(file, include_completed)
            if dicts:
                encoding_dicts[file.id] = dicts
        return encoding_dicts

    def get_all_associated_video_dicts(self, media_files, include_completed=False):
        video_dicts = {}
        for file in media_files:
            dicts = self.get_associated_video_dicts(file, include_completed)
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

    def transcode_media_file(self, media_file, profile_ids=None, state_update_url=None):
        from pylons import app_globals
        from mediacore.lib.helpers import url_for
        download_url = media_file.link_url(qualified=True, static=True)
        if not profile_ids:
            profile_names = [x.strip() for x in app_globals.settings['panda_encoding_profiles'].split(',')]
            profile_ids = self.profile_names_to_ids(profile_names)
        transcode_details = self.client.transcode_file(download_url, profile_ids, state_update_url)
        self.associate_video_id(media_file, transcode_details['id'])

    def _media_file_from_encoding_or_video_dict(self, d, media, display_name, base_url):
        from mediacore.lib.mediafiles import add_new_media_file
        extname = d['extname']
        if extname == '.ts':
            # Panda reports multi-bitrate http streaming encodings as .ts file
            # but the associated playlist is the only thing ipods, etc, can read.
            extname = '.m3u8'
        new_filename = "%s%s" % (d['id'], d['extname'])
        url = base_url + new_filename
        new_media_file = add_new_media_file(media, url=url, already_encoded=True)
        new_media_file.height = d['height']
        new_media_file.width = d['width']
        new_media_file.size = d['file_size']
        ba = d.get('audio_bitrate', None) or 0
        bv = d.get('video_bitrate', None) or 0
        new_media_file.max_bitrate = (ba + bv) or None
        new_media_file.display_name = display_name
        return new_media_file

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
        if v['status'] != 'success':
            return
        if any(e['status'] != 'success' for e in encodings):
            return
        # TODO: Figure out if the second check is actually necessary.
        #       What about the case where a new encoding item is added to a
        #       previously 'success'ful video.

        # Set the media's duration based on the video file.
        if v['duration'] and not media_file.media.duration:
            media_file.media.duration = v['duration']/1000

        original_filename = os.path.splitext(media_file.display_name)[0].strip()
        # For each successful encoding (and the original file), create a new MediaFile
        for base_url, url_type in s3_base_urls():
            display_name = "(%s) %s%s" % (url_type, original_filename, v['extname'])
            new_mf = self._media_file_from_encoding_or_video_dict(v, media_file.media, display_name, base_url)
            self.associate_video_id(new_mf, v['id'], ENCODING_COMPLETE)

            for e in encodings:
                profile = self.client.get_profile(e['profile_id'])
                display_name = "(%s - %s) %s%s" % (url_type, profile['name'], original_filename, e['extname'])
                new_mf = self._media_file_from_encoding_or_video_dict(e, media_file.media, display_name, base_url)
                self.associate_encoding_id(new_mf, e['id'], ENCODING_COMPLETE)

        self.disassociate_video_id(media_file, v['id'])
        # TODO: Now delete the exisitng media_file?
        media_file.height = v['height']
        media_file.width = v['width']
        media_file.size = v['file_size']
