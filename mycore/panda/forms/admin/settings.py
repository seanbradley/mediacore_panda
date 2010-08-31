from pylons.i18n import N_ as _

from mediacore.forms import ListFieldSet, ListForm, ResetButton, SubmitButton, TextField
from mediacore.forms.admin.settings import boolean_radiobuttonlist

class PandaForm(ListForm):
    template = 'mediacore.templates.admin.box-form'
    id = 'settings-form'
    css_class = 'form'
    submit_text = None
    fields = [
        boolean_radiobuttonlist('panda_transcoding_enabled', label_text=_('Automatically transcode uploaded videos using Panda')),
        ListFieldSet('panda', suppress_label=True, legend=_('Panda Account Details:'), css_classes=['details_fieldset'], children=[
            TextField('panda_cloud_id', maxlength=255, label_text=_('Cloud ID')),
            TextField('panda_access_key', maxlength=255, label_text=_('Access Key')),
            TextField('panda_secret_key', maxlength=255, label_text=_('Secret Key')),
        ]),
        TextField('panda_encoding_profiles', label_text=_('Encodings to use (comma-separated list of encoding names)')),
        ListFieldSet('amazon', suppress_label=True, legend=_('Amazon CloudFront Domains (e.g. a1b2c3d4e5f6.cloudfront.net):'), css_classes=['details_fieldset'], children=[
            TextField('panda_amazon_cloudfront_download_domain', maxlength=255, label_text=_('CloudFront HTTP')),
            TextField('panda_amazon_cloudfront_streaming_domain', maxlength=255, label_text=_('CloudFront RTMP')),
        ]),
        SubmitButton('save', default=_('Save'), css_classes=['btn', 'btn-save', 'f-rgt']),
        ResetButton('cancel', default=_('Cancel'), css_classes=['btn', 'btn-cancel']),
    ]

