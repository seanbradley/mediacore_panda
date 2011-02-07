from setuptools import setup, find_packages

setup(
    name = 'MediaCore-Panda',
    version = '0.1',
    packages = find_packages(),
    author = 'Anthony Theocharis',
    author_email = 'anthony@simplestation.com',
    description = 'A MediaCore plugin for using the Panda online transcoding service with Amazon S3.',
    install_requires = [
        'simplejson',
        'panda == 0.1.2',
    ],
    entry_points = '''
        [mediacore.plugin]
        panda=mediacore_panda
    '''
)
