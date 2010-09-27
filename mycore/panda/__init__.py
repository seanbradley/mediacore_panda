import logging
import simplejson

from paste.deploy.converters import asbool
from pylons import app_globals

from mediacore.plugin import events
from mediacore.plugin.events import observes
from mycore.panda.lib import PandaHelper, PandaException, PandaStorage

log = logging.getLogger(__name__)

@observes(events.Environment.routes)
def add_routes(mapper):
    mapper.connect('/admin/plugins/panda',
        controller='panda/admin/settings',
        action='panda')
    mapper.connect('/admin/plugins/panda/save',
        controller='panda/admin/settings',
        action='panda_save')

@observes(events.Admin.MediaController.edit)
def add_edit_vars(**result):
    return add_panda_vars(**result)

def add_panda_vars(media, **result):
    result['media'] = media
    result['encoding_dicts'] = {}
    result['video_dicts'] = {}
    result['profile_names'] = {}
    for file in media.files:
        if isinstance(file.storage, PandaStorage):
            result['encoding_dicts'][file.id] = \
                file.storage.panda_helper.get_associated_encoding_dicts(file)
            result['video_dicts'][file.id] = \
                file.storage.panda_helper.get_associated_video_dicts(file)
            if not result['profile_names']:
                result['profile_names'] = \
                    file.storage.panda_helper.get_profile_ids_names()
    return result

from mediacore.lib import players
from mediacore.lib.helpers import url_for

# Do whatever monkeypatching you need to do.
