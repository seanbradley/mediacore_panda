from formencode import Invalid
from pylons.i18n import N_ as _

from mediacore.forms import CheckBoxList, ListFieldSet, TextField
from mediacore.forms.admin.storage import StorageForm
from mediacore.forms.admin.settings import real_boolean_radiobuttonlist as boolean_radiobuttonlist
from mediacore.lib.helpers import merge_dicts
from mediacore.model.meta import DBSession

from mediacore_panda.lib import PandaHelper, PandaException
from mediacore_panda.lib.storage import (CLOUDFRONT_DOWNLOAD_URI,
    CLOUDFRONT_STREAMING_URI, PANDA_ACCESS_KEY, PANDA_CLOUD_ID, PANDA_PROFILES,
    PANDA_SECRET_KEY, S3_BUCKET_NAME)


class ProfileCheckBoxList(CheckBoxList):
    css_classes = ['checkboxlist']
    params = ['profiles']
    template = 'panda/admin/profile_checkboxlist.html'

class PandaForm(StorageForm):
    template = 'panda/admin/storage.html'
    fields = StorageForm.fields + [
        ListFieldSet('panda', suppress_label=True, legend=_('Panda Account Details:'), children=[
            TextField('cloud_id', maxlength=255, label_text=_('Cloud ID')),
            TextField('access_key', maxlength=255, label_text=_('Access Key')),
            TextField('secret_key', maxlength=255, label_text=_('Secret Key')),
        ]),
        ListFieldSet('s3', suppress_label=True, legend=_('Amazon S3 Details:'), children=[
            TextField('bucket_name', maxlength=255, label_text=_('S3 Bucket Name')),
        ]),
        ListFieldSet('cloudfront',
            suppress_label=True,
            legend=_('Amazon CloudFront Domains (e.g. a1b2c3d4e5f6.cloudfront.net):'),
            help_text=_('If you intend to use CloudFront to serve these files, please ensure that the CloudFront domains you enter below refer to this S3 bucket.'),
            children=[
            TextField('streaming_uri', maxlength=255, label_text=_('CloudFront Streaming Domain')),
            TextField('download_uri', maxlength=255, label_text=_('CloudFront Download Domain')),
        ]),
        ProfileCheckBoxList('profiles', label_text=_('Encodings to use')),
    ] + StorageForm.buttons

    def display(self, value, engine, **kwargs):
        try:
            profiles = engine.panda_helper().client.get_profiles()
            cloud = engine.panda_helper().client.get_cloud()
        except PandaException:
            profiles = None
            cloud = None

        if not value:
            value = {}

        merged_value = {}
        merge_dicts(merged_value, {
            'panda': {
                'cloud_id': engine._data[PANDA_CLOUD_ID],
                'access_key': engine._data[PANDA_ACCESS_KEY],
                'secret_key': engine._data[PANDA_SECRET_KEY],
            },
            's3': {
                'bucket_name': engine._data[S3_BUCKET_NAME],
            },
            'cloudfront': {
                'streaming_uri': engine._data[CLOUDFRONT_STREAMING_URI],
                'download_uri': engine._data[CLOUDFRONT_DOWNLOAD_URI],
            },
            'profiles': engine._data[PANDA_PROFILES],
        }, value)

        merged_kwargs = {}
        merge_dicts(merged_kwargs, {
            'cloud': cloud,
            'child_args': {
                'profiles': {'profiles': profiles},
            },
        }, kwargs)

        # kwargs are vars for the template, value is a dict of values for the form.
        return StorageForm.display(self, merged_value, engine, **merged_kwargs)

    def save_engine_params(self, engine, panda, s3, cloudfront, profiles, **kwargs):
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
        # The panda client library expects strings.
        for key in panda:
            if panda[key] is None:
                panda[key] = u''

        StorageForm.save_engine_params(self, engine, **kwargs)
        engine._data[PANDA_CLOUD_ID] = panda['cloud_id']
        engine._data[PANDA_ACCESS_KEY] = panda['access_key']
        engine._data[PANDA_SECRET_KEY] = panda['secret_key']
        engine._data[PANDA_PROFILES] = profiles
        engine._data[S3_BUCKET_NAME] = s3['bucket_name']
        engine._data[CLOUDFRONT_STREAMING_URI] = cloudfront['streaming_uri']
        engine._data[CLOUDFRONT_DOWNLOAD_URI] = cloudfront['download_uri']

        engine.panda_helper.cache.clear()
        try:
            engine.panda_helper().client.get_cloud()
        except PandaException, e:
            DBSession.rollback()
            # TODO: Display this error to the user.
            raise Invalid(str(e), None, None)
