from setuptools import setup, find_packages

setup(
    name = 'MyCorePanda',
    version = '0.1',
    packages = find_packages(),
    namespace_packages = ['mycore'],
    author = 'Anthony Theocharis',
    author_email = 'anthony@simplestation.com',
    description = 'A MediaCore plugin for using the Panda online transcoding service with Amazon S3.',
    install_requires = [
        'simplejson',
        'panda == 0.1.2',
    ],
    entry_points = '''
        [mediacore.plugin]
        panda=mycore.panda
    '''
)
