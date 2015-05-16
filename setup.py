"""
    Surveying PyPI is fun!

    :copyright: (c) 2015 by Mahmoud Hashemi
    :license: BSD, see LICENSE for more details.
"""

from setuptools import setup


__author__ = 'Mahmoud Hashemi'
__version__ = '0.0.4dev'
__contact__ = 'mahmoudrhashemi@gmail.com'
__url__ = 'https://github.com/mahmoud/versify'
__license__ = 'BSD'


if __name__ == '__main__':
    setup(name='versify',
          version=__version__,
          description="Maintenance utility for tumblr blogs.",
          long_description=__doc__,
          author=__author__,
          author_email=__contact__,
          url=__url__,
          packages=['versify'],
          install_requires=['ashes==0.7.3',
                            'boltons==0.4.2',
                            'gevent==1.0.1',
                            'progressbar==2.3',
                            'PyTumblr==0.0.6',
                            'PyYAML==3.11'],
          include_package_data=True,
          zip_safe=False,
          license=__license__,
          platforms='any',
          classifiers=[
              'Topic :: Software Development :: Libraries',
              'Programming Language :: Python :: 2.7', ])
