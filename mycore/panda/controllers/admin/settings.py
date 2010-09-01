import logging

from repoze.what.predicates import has_permission

from mediacore.lib.base import BaseSettingsController
from mediacore.lib.decorators import expose, validate
from mediacore.lib.helpers import url_for
from mediacore.model.settings import fetch_and_create_multi_setting
from mycore.panda.forms.admin.settings import PandaForm
from mycore.panda.lib import PandaException, PandaHelper
from paste.deploy.converters import asbool

log = logging.getLogger(__name__)

panda_form = PandaForm()

class SettingsController(BaseSettingsController):
    allow_only = has_permission('admin')

    @expose('panda/admin/settings/panda.html')
    def panda(self, **kwargs):
        profiles = None
        cloud = None
        try:
            ph = PandaHelper(ignore_enabled=True)
            profiles = ph.client.get_profiles()
            cloud = ph.client.get_cloud()
        except PandaException, e:
            pass
        kwargs['profiles'] = profiles
        kwargs['cloud'] = cloud

        action = url_for(controller='/panda/admin/settings', action='panda_save')
        return self._display(panda_form, values=kwargs, action=action)

    @expose()
    @validate(panda_form, error_handler=panda)
    def panda_save(self, **kwargs):
        """Save :class:`~mycore.panda.forms.admin.settings.PandaForm`."""
        # Only allow panda_transcoding_enabled to be set to 'true' if the
        # account details are verified to work.
        trans = kwargs.get('panda_transcoding_enabled', 'false')
        trans_temp = 'false'
        if asbool(trans):
            cloud_id = kwargs.get('panda.panda_cloud_id', None)
            access_key = kwargs.get('panda.panda_access_key', None)
            secret_key = kwargs.get('panda.panda_secret_key', None)
            if all((cloud_id, access_key, secret_key)):
                try:
                    ph = PandaHelper(cloud_id, access_key, secret_key, ignore_enabled=True)
                    ph.client.get_cloud()
                    # If we got to this point, the account works.
                    trans_temp = 'true'
                except PandaException, e:
                    pass
        kwargs['panda_transcoding_enabled'] = trans_temp
        # Ensure that the selected cloudfront streaming server is in the known
        # RTMP list.
        if 'amazon.panda_amazon_cloudfront_streaming_domain' in kwargs:
            new_rtmp_url = 'rtmp://' + kwargs['amazon.panda_amazon_cloudfront_streaming_domain'].rstrip('/') + '/cfx/st'
            ms = fetch_and_create_multi_setting(u'rtmp_server', new_rtmp_url)
        return self._save(panda_form, 'panda', values=kwargs)
