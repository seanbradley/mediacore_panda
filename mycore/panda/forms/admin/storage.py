from pylons.i18n import N_ as _

from mediacore.forms import ListFieldSet, TextField
from mediacore.forms.admin.storage import StorageForm
from mediacore.forms.admin.settings import real_boolean_radiobuttonlist as boolean_radiobuttonlist
from mediacore.lib.helpers import merge_dicts

from mycore.panda.lib import PandaHelper, PandaException

class PandaForm(StorageForm):
    template = 'panda/admin/storage.html'
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
        StorageForm.save_engine_params(self, engine, **kwargs)
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

