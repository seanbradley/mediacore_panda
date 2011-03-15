# This file is a part of MediaCore-Panda, Copyright 2011 Simple Station Inc.
#
# MediaCore is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MediaCore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

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
