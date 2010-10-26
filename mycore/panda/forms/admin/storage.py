from formencode import Invalid
from pylons.i18n import N_ as _

from mediacore.forms import CheckBoxList, ListFieldSet, TextField
from mediacore.forms.admin.storage import StorageForm
from mediacore.forms.admin.settings import real_boolean_radiobuttonlist as boolean_radiobuttonlist
from mediacore.lib.helpers import dict_merged_with_defaults

from mycore.panda.lib import PandaHelper, PandaException
from mycore.panda.lib.storage import (PANDA_ACCESS_KEY, PANDA_SECRET_KEY,
    PANDA_CLOUD_ID, PANDA_PROFILES, PANDA_AUTO_TRANSCODE,
    S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET_NAME,
    CLOUDFRONT_DOWNLOAD_URI, CLOUDFRONT_STREAMING_URI)


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
#            TextField('access_key', maxlength=255, label_text=_('S3 Access Key')),
#            TextField('secret_key', maxlength=255, label_text=_('S3 Secret Key')),
        ]),
        ListFieldSet('cloudfront',
            suppress_label=True,
            legend=_('Amazon CloudFront Domains (e.g. a1b2c3d4e5f6.cloudfront.net):'),
            help_text=_('If you intend to use CloudFront to serve these files, please ensure that the CloudFront domains you enter below refer to this S3 bucket.'),
            children=[
            TextField('streaming_uri', maxlength=255, label_text=_('CloudFront Streaming Domain')),
            TextField('download_uri', maxlength=255, label_text=_('CloudFront Download Domain')),
        ]),
#        ListFieldSet('encoding', suppress_label=True, legend=_('Encoding Options:'), children=[
        ProfileCheckBoxList('profiles', label_text=_('Encodings to use')),
#            CheckBox('auto_transcode', label_text=_('Automatically transcode uploaded videos using Panda')),
#        ]),
    ] + StorageForm.buttons

    def display(self, value, **kwargs):
        engine = kwargs['engine']

        try:
            profiles = engine.panda_helper().client.get_profiles()
            cloud = engine.panda_helper().client.get_cloud()
        except PandaException:
            profiles = None
            cloud = None

        value = dict_merged_with_defaults(value or {}, {
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
        })

        kwargs = dict_merged_with_defaults(kwargs, {
            'cloud': cloud,
            'child_args': {
                'profiles': {'profiles': profiles},
            },
        })

        # kwargs are vars for the template, value is a dict of values for the form.
        return StorageForm.display(self, value, **kwargs)

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
            # TODO: Display this error to the user.
            error, message = e
            raise Invalid(str(e), None, None)
