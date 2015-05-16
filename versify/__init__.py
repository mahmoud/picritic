
import gevent.monkey
gevent.monkey.patch_all()
from gevent.pool import Pool

import os
import re
import json
import time
import urllib2
import argparse
from os.path import expanduser

from boltons.dictutils import OMD
from boltons.osutils import mkdir_p
from progressbar import ProgressBar, Bar, Percentage, SimpleProgress


DEFAULT_CONCURRENCY = 20

DEFAULT_HOME_PATH = '~/.versify/'

# TODO: loggiiiiing
# TODO: resumability of fetch + use boltons.atomic_save

DEFAULT_PYPI_URL = 'https://pypi.python.org/'
DEFAULT_PKG_LIST_URL = DEFAULT_PYPI_URL + 'simple/'

PKG_IDX_FILENAME = 'package_index.json'
PKG_INFO_FILENAME = 'package_info.jsonl'
PKG_ERR_FILENAME = 'package_errors.jsonl'

"""
If --clean:
  - mkdir_p the _bak directory
  - move index
  - move infos and err


"""

class PackageInfo(object):
    def __init__(self, info_dict):
        self.info_dict = info_dict
        self.version = info_dict.get('version')
        self.daily_hits = info_dict.get('downloads', {}).get('last_day')


class PackageIndex(object):
    def __init__(self, package_rel_urls, url, last_fetched):
        self.package_rel_urls = package_rel_urls
        self.last_fetched = last_fetched or time.time()
        self.url = url

    @classmethod
    def from_html(cls, html, url, last_fetched=None):
        # find all hrefs
        package_rel_urls = get_hrefs(html)  # relative urls
        return cls(package_rel_urls, url, last_fetched)

    @classmethod
    def from_path(cls, path):
        with open(path) as f:
            pkg_idx = json.load(f)
            ret = cls(pkg_idx['packages'],
                      pkg_idx['url'],
                      pkg_idx['last_fetched'])
        ret.path = path
        return ret

    def __len__(self):
        return len(self.package_rel_urls)

    def __iter__(self):
        return iter(self.package_rel_urls)

    def save(self, path=None):
        if not self.path and not path:
            raise ValueError('no path set')
        self.path = path or self.path
        with open(self.path, 'w') as f:
            to_write = {'packages': self.package_names,
                        'last_fetched': self.last_fetched,
                        'url': self.url}
            json.dump(to_write, f)
        return


class PackageInfoState(object):
    pass


_href_re = re.compile('href=[\"\'](?P<href>[^\"\']*)[\"\']')


def get_hrefs(html):
    # turns links into canonical links for RSS
    return _href_re.findall(html)



class Versify(object):

    _pkg_type = PackageInfo
    _pkg_idx_type = PackageIndex

    def __init__(self, home_path=DEFAULT_HOME_PATH, **kwargs):
        self.default_pypi_url = kwargs.pop('pypi_url', DEFAULT_PYPI_URL)
        self.default_action = kwargs.pop('action', None)

        self.concurrency = kwargs.pop('concurrency', DEFAULT_CONCURRENCY)

        self.home_path = expanduser(home_path)
        if kwargs:
            raise TypeError('unexpected keyword arguments: %r' % kwargs.keys())

        if not os.path.exists(self.home_path):
            mkdir_p(self.home_path)

        self.index_path = self.home_path + PKG_IDX_FILENAME
        self.package_info_path = self.home_path + PKG_INFO_FILENAME
        self.client = ''

    def load_index(self):
        idx_type = self._pkg_idx_type
        return idx_type.from_path(self.home_path + PKG_IDX_FILENAME)

    def fetch_package_infos(self):
        pkg_infos = OMD()
        get_url = urllib2.urlopen
        pool = Pool(self.concurrency)
        index_url = self.default_pypi_url + 'simple/'
        resp = get_url(index_url)
        index_html = resp.read()
        package_index = PackageIndex.from_html(index_html, index_url)

        pb = ProgressBar(
            widgets=[Percentage(),
                     ' ', Bar(),
                     ' ', SimpleProgress()],
            maxval=len(package_index) + 1).start()
        pb.update(0)

        def _get_package_info(package_rel_url):
            pkg_url = self.default_pypi_url + 'pypi/%s/json' % package_rel_url
            try:
                resp = get_url(pkg_url)
            except Exception as e:
                return {'error': repr(e), 'rel_url': package_rel_url}
            return json.loads(resp.read())

        pkg_info_iter = pool.imap_unordered(_get_package_info, package_index)
        err_count = 0
        for pkg_info in pkg_info_iter:
            try:
                pkg_infos[pkg_info['info']['name']] = pkg_info
            except KeyError:
                err_count += 1
                pkg_infos[pkg_info['rel_url']] = pkg_info
            pb.update(len(pkg_infos))

        pool.join(timeout=0.3, raise_error=True)
        print 'Done fetching. Saving', len(pkg_info), 'package_infos.'
        import pdb;pdb.set_trace()
        return

    @classmethod
    def get_argparser(cls):
        prs = argparse.ArgumentParser()
        subprs = prs.add_subparsers(dest='action',
                                    help='versify supports fetch and report'
                                    ' subcommands')
        subprs.add_parser('fetch',
                          help='fetch and save a local version of an index')
        subprs.add_parser('report',
                          help='generate a report about index versions')

        add_arg = prs.add_argument
        add_arg('--home', default=DEFAULT_HOME_PATH,
                help='versify home path, with cached index, etc.'
                'defaults to ~/.versify')
        add_arg('--conc', default=DEFAULT_CONCURRENCY,
                help='number of concurrent requests to allow during fetches')
        return prs

    @classmethod
    def from_args(cls):
        kwarg_map = {'conc': 'concurrency',
                     'home': 'home_path'}
        prs = cls.get_argparser()
        kwargs = dict(prs.parse_args()._get_kwargs())
        for src, dest in kwarg_map.items():
            kwargs[dest] = kwargs.pop(src)
        return cls(**kwargs)
