
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
from boltons.tbutils import ExceptionInfo
from boltons.osutils import mkdir_p
from boltons.jsonutils import JSONLIterator
from progressbar import ProgressBar, Bar, Percentage, SimpleProgress


DEFAULT_CONCURRENCY = 100

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


class PackageIndex(object):
    def __init__(self, package_rel_urls, url, last_fetched):
        self.package_rel_urls = sorted(package_rel_urls)
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
            ret = cls(pkg_idx['rel_urls'],
                      pkg_idx['url'],
                      pkg_idx['last_fetched'])
        ret.path = path
        return ret

    @classmethod
    def from_dict(cls, pkg_idx):
        return cls(pkg_idx['rel_urls'],
                   pkg_idx['url'],
                   pkg_idx['last_fetched'])

    def __len__(self):
        return len(self.package_rel_urls)

    def __iter__(self):
        return iter(self.package_rel_urls)

    def to_dict(self):
        return {'rel_urls': self.package_rel_urls,
                'url': self.url,
                'last_fetched': self.last_fetched}


class PackageInfoMap(object):
    def __init__(self, pkg_infos=None, pkg_idx=None, last_fetched=None):
        self.pkg_infos = pkg_infos or OMD()
        self.pkg_idx = pkg_idx or None
        self.last_fetched = last_fetched or time.time()
        self.path = None
        self.last_saved = None

    def add_dict(self, pkg_info_dict):
        self.pkg_infos[pkg_info_dict['rel_url']] = pkg_info_dict

    def __len__(self):
        return len(self.pkg_infos)

    def __iter__(self):
        return iter(self.pkg_infos)

    @classmethod
    def from_path(cls, path):
        with open(path) as f:
            jsonl_iter = JSONLIterator(f)
            pkg_idx = next(jsonl_iter)
            ret = cls(pkg_idx=PackageIndex.from_dict(pkg_idx))
            ret.path = path
            for pkg_dict in jsonl_iter:
                ret.add_dict(pkg_dict)
                ret.last_saved = pkg_dict['rel_url']
        return ret

    def save(self, path=None):
        if not self.path and not path:
            raise ValueError('no path set')
        self.path = path or self.path

        if not self.last_saved:
            with open(self.path, 'w') as f:
                # TODO: fetch/count metadata on last line
                pkg_idx = self.pkg_idx.to_dict()
                f.write(json.dumps(pkg_idx, sort_keys=True))
                f.write('\n')
        with open(self.path, 'a') as f:
            writing = False
            for pkg_name, pkg_info in self.pkg_infos.items():
                if not self.last_saved or pkg_name == self.last_saved:
                    writing = True
                if writing:
                    json.dump(pkg_info, f, sort_keys=True)
                    f.write('\n')
                    self.last_saved = pkg_info['rel_url']
        return


_href_re = re.compile('href=[\"\'](?P<href>[^\"\']*)[\"\']')


def get_hrefs(html):
    # turns links into canonical links for RSS
    return _href_re.findall(html)


class Versify(object):
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

    @property
    def package_index(self):
        try:
            return self.pkg_info_map.pkg_idx
        except AttributeError:
            return None

    def load(self):
        pkg_info_map = None
        if os.path.exists(self.package_info_path):
            pkg_info_map = PackageInfoMap.from_path(self.package_info_path)
        self.pkg_info_map = pkg_info_map

    def _fetch_package_index(self):
        index_url = self.default_pypi_url + 'simple/'
        resp = urllib2.urlopen(index_url)
        index_html = resp.read()
        return PackageIndex.from_html(index_html, index_url)

    def fetch_package_infos(self):
        self.load()
        get_url = urllib2.urlopen
        pool = Pool(self.concurrency)
        pkg_idx = self.package_index
        if not pkg_idx:
            pkg_idx = self._fetch_package_index()
        pkg_info_map = self.pkg_info_map
        if not pkg_info_map:
            pkg_info_map = PackageInfoMap(pkg_idx=pkg_idx)
            pkg_info_map.path = self.package_info_path
        pb = ProgressBar(widgets=[Percentage(),
                                  ' ', Bar(),
                                  ' ', SimpleProgress()],
                         maxval=len(pkg_idx) + 1)
        pb.start()
        pb.update(len(pkg_info_map))

        to_fetch = sorted(set(pkg_idx.package_rel_urls) -
                          set(pkg_info_map.pkg_infos.viewkeys()))

        def _get_package_info(package_rel_url):
            pkg_url = self.default_pypi_url + 'pypi/%s/json' % package_rel_url
            try:
                resp = get_url(pkg_url)
            except Exception as e:
                ret = {'error': repr(e)}
            else:
                ret = json.loads(resp.read())
            ret['rel_url'] = package_rel_url
            return ret

        pkg_info_iter = pool.imap(_get_package_info, to_fetch)
        err_count = 0
        for pkg_info in pkg_info_iter:
            try:
                pkg_info_map.add_dict(pkg_info)
            except KeyError:
                err_count += 1
                pkg_info_map.add_dict(pkg_info)
            pb.update(len(pkg_info_map))
            if len(pkg_info_map) % self.concurrency == 0:
                pkg_info_map.save()

        pool.join(timeout=0.3, raise_error=True)
        print 'Done fetching. Saving', len(pkg_info_map), 'package infos.'
        try:
            pkg_info_map.save()
        except Exception:
            print ExceptionInfo.from_current().get_formatted()
            import pdb;pdb.post_mortem()
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
