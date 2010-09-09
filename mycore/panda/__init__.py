import logging
import simplejson

from paste.deploy.converters import asbool
from pylons import app_globals

from mediacore.model.settings import insert_settings
from mediacore.plugin import events
from mediacore.plugin.events import observes
from mycore.panda.lib import PandaHelper, PandaException

log = logging.getLogger(__name__)

@observes(events.Environment.routes)
def add_routes(mapper):
    mapper.connect('/admin/plugins/panda',
        controller='panda/admin/settings',
        action='panda')
    mapper.connect('/admin/plugins/panda/save',
        controller='panda/admin/settings',
        action='panda_save')

@observes(events.plugin_settings_links)
def panda_setting_link():
    yield 'Panda Transcoding', url_for(controller='/panda/admin/settings', action='panda')

@observes(events.Environment.init_model)
def create_settings():
    insert_settings([
        (u'panda_access_key', u''),
        (u'panda_secret_key', u''),
        (u'panda_transcoding_enabled', u'false'),
        (u'panda_cloud_id', u''),
        (u'panda_encoding_profiles', u'h264'),
        (u'panda_amazon_cloudfront_download_domain', u''),
        (u'panda_amazon_cloudfront_streaming_domain', u''),
    ])

@observes(events.Admin.MediaController.edit)
def add_edit_vars(**result):
    media = result['media']
    try:
        ph = PandaHelper()
        result['encoding_dicts'] = ph.get_all_associated_encoding_dicts(media.files)
        result['video_dicts'] = ph.get_all_associated_video_dicts(media.files)
        result['profile_names'] = ph.get_profile_ids_names()
    except PandaException, e:
        result['encoding_dicts'] = None
        result['video_dicts'] = None
        result['profile_names'] = None
    return result

@observes(events.EncodeMediaFile)
def panda_transcode(media_file):
    # send the file to Panda for encoding, if necessary
    if asbool(app_globals.settings['panda_transcoding_enabled']):
        state_update_url = url_for(
            controller='/panda/admin/media',
            action='panda_update',
            file_id=media_file.id,
# For some reason, the $ on this next line always gets urlencoded, and panda ignores it.
#            video_id='$id',
            qualified=True
        )
        ph = PandaHelper()
        ph.transcode_media_file(media_file, state_update_url=state_update_url)

from mediacore.lib import players
from mediacore.lib.helpers import url_for

# Do whatever monkeypatching you need to do.
