#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import urllib.parse
import xmlrpc.client
import shlex

from lxml.html import clean
from dotenv import load_dotenv
from html_sanitizer import Sanitizer, sanitizer
import pypandoc
from bs4 import BeautifulSoup, Comment
from multiprocessing import Pool
from bleach.sanitizer import Cleaner
import bleach

'''
TRAC_URL=http://trac.sasview.org/wiki/WikiStart python3 export-wiki.py

 'wiki.getRecentChanges',
 'wiki.getRPCVersionSupported',
 'wiki.getPage',
 'wiki.getPageVersion',
 'wiki.getPageHTML',
 'wiki.getPageHTMLVersion',
 'wiki.getAllPages',
 'wiki.getPageInfo',
 'wiki.getPageInfoVersion',
 'wiki.putPage',
 'wiki.listAttachments',
 'wiki.getAttachment',
 'wiki.putAttachment',
 'wiki.putAttachmentEx',
 'wiki.deletePage',
 'wiki.deleteAttachment',
 'wiki.listLinks',
 'wiki.wikiToHtml',

'''

NUMBER_OF_CORES = 7

OUTPUT_DIRECTORY = "out"

# load .env file
load_dotenv()

# HTML Sanitizer options
# Make a copy
mysettings = dict(sanitizer.DEFAULT_SETTINGS)
# Add your changes
mysettings['tags'].update(['img', 'table', 'tr', 'th', 'td', 'pre'])
mysettings['empty'].update(['img', 'table', 'tr', 'th', 'td'])
mysettings['separate'].update(['tr', 'th', 'td'])
mysettings['attributes'].update({
    'img': ('src', ),
    'td': ('style', )
})

def process_single_file(page):
    
    # TODO remove
    # if page not in ["ListofModels", 'WikiStart']:
    #     return

    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)

    mysanitizer = Sanitizer(settings=mysettings) 
    
    filename = f'{page}.html'.lstrip('/')
    filename = filename.replace('/', '_')
    filename = os.path.join(OUTPUT_DIRECTORY, filename)

    print("Saving: {}".format(filename))
    dir = os.path.dirname(filename)
    dir and os.makedirs(dir, exist_ok=True)
    with open(filename, 'w') as f:
        # doc is a string
        doc = trac.wiki.getPageHTML(page)

        # Write a copy of the original
        with open(filename.replace(".html","_orig.html"), 'w') as f2:
            f2.write(doc)

        #doc = mysanitizer.sanitize(doc)
        cleaner = Cleaner(tags=bleach.sanitizer.ALLOWED_TAGS+[
            'pre', 'table', 'tr', 'td', 'th',
            "a", "h1", "h2", "h3", "strong", "em", "p", "ul", "ol",
            "li", "br", "sub", "sup", "hr"], strip=True, strip_comments=True)
        doc = cleaner.clean(doc)
        
        # Regex here!!!
        def replacer(match):
            ''' To replace in the matched groups. Only way that I found....'''
            return 'href="{}{}"'.format(
                match.group(1),
                match.group(2) if match.group(2) is not None else ''
            ).replace('/', '_')

        doc = re.sub(
            r'''href=\"http://trac\.sasview\.org/wiki/([a-zA-Z0-9/]+)(#[a-zA-Z0-9]+)?\"''',
            replacer, doc)
        
        soup = BeautifulSoup(doc, features="lxml")
        doc = soup.prettify()
        
        f.write(doc)
    return filename

def main():
    
    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)

    pages  = list(trac.wiki.getAllPages())
    with Pool(NUMBER_OF_CORES) as p:
        print(p.map(process_single_file, pages))
        
        
    # script to convert html to md and update github
    script = '''cd /Users/rhf/git/sasview_scripts/trac_migration/out
for f in *.html; do
    outfile="${f/.html/.md}"
    command="/usr/local/bin/pandoc -s '$f' -t markdown-simple_tables-multiline_tables-pipe_tables -o '$outfile'"
    echo "$command"
    output=$(eval "$command")
    echo "$output"
done
if [ -f WikiStart.md ]; then
    mv WikiStart.md Home.md
fi
cp *.md /tmp/temp.wiki/
cd /tmp/temp.wiki/
git add .
date=$(date)
git commit -m 'Added MD script ${date}'
git push
cd /Users/rhf/git/sasview_scripts/trac_migration

    '''

    os.system(script)

__name__ == '__main__' and main()
