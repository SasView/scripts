#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import urllib.parse
import xmlrpc.client
import shlex

from lxml.html import clean
from dotenv import load_dotenv
import pypandoc
from bs4 import BeautifulSoup, Comment
from multiprocessing import Pool
from bleach.sanitizer import Cleaner
import bleach
import re
from github import Github
import time
import random


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

DEFAULT_GITHUB_USERNAME = "sasview-bot"

# load .env file
load_dotenv()




def update_issues_map():
    '''
    Query github and gets a map of 
    [ticket number] = (repo name, issue_number)
    '''
    
    github_username = DEFAULT_GITHUB_USERNAME
    GITHUB_TOKENS = eval(os.getenv("GITHUB-TOKENS"))
    token = GITHUB_TOKENS[github_username]
    github = Github(token)
    github_org = github.get_organization(os.getenv("GITHUB-ORGANISATION"))
    
    repos = ["sasview", 'sasmodels', 'sasmodel-marketplace']
    issues_map = {}
    for repo_name in repos:
        print("Updating issues map with the repository:", repo_name)
        github_repo = github_org.get_repo(repo_name)
        for i in github_repo.get_issues(state="all") :
            ticket_number = re.findall('.*\(Trac #(\d+)\)',  i.title)
            if ticket_number:
                issues_map[int(ticket_number[0])] = (repo_name, i.number)
    return issues_map

issues_map = update_issues_map()

def update_ticket_link_to_gh_issues(text):
    '''
    replaces: http://trac.sasview.org/ticket/{###}
    with: Sasview/{REPO}#{####} 
    '''

    #pattern = r'https*:\/\/trac\.sasview\.org\/ticket\/(\d+)\/*'
    pattern = r"""<a.*href=["']https*:\/\/trac\.sasview\.org\/ticket\/(\d+)\/*["'].*>(.+)<\/a>"""
    
    # Regex here!!!
    def replacer(match):
        ''' To replace in the matched groups. Only way that I found....'''

        repo_name, issue_number = issues_map.get(int(match.group(1)), (None, None))
        # /user/project/issues/1
        #to_replace = 'SasView/{}#{}'.format(repo_name, issue_number)
        to_replace = r'<a href="/SasView/{0}/issues/{1}">SasView/{0}#{1}</a>'.format(repo_name, issue_number)
        return to_replace

    text_corrected = re.sub(pattern, replacer, text)
    return text_corrected

def process_single_file(page):
    
    # # TODO remove
    # matches = [
    # # "ListofModels", 
    # # 'WikiStart', 
    # # "CondaDevSetup",
    #     "TutorialsTNG",
    # ]
    # if not any(s in page for s in matches):
    #     return
    
    filename = f'{page}.html'.lstrip('/')
    filename = filename.replace('/', '_')
    filename = os.path.join(OUTPUT_DIRECTORY, filename)

    print("Saving: {}".format(filename))
    dir = os.path.dirname(filename)
    dir and os.makedirs(dir, exist_ok=True)
    with open(filename, 'w') as f:
        trac_url = os.getenv("TRAC-URL")
        rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
        trac = xmlrpc.client.ServerProxy(rpc_url)
        # doc is a string
        doc = trac.wiki.getPageHTML(page)

        # Write a copy of the original
        with open(filename.replace(".html","_orig.html"), 'w') as f2:
            f2.write(doc)
        
        doc = update_ticket_link_to_gh_issues(doc)

        #doc = mysanitizer.sanitize(doc)
        cleaner = Cleaner(tags=bleach.sanitizer.ALLOWED_TAGS+[
            'pre', 'table', 'tr', 'td', 'th', 'tt', 'dl', 'dt', 'dd',
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




def main():

    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)

    pages  = list(trac.wiki.getAllPages())
    with Pool(NUMBER_OF_CORES) as p:
        p.map(process_single_file, pages)
        
    # script to convert html to md and update github
    script = '''cd /Users/rhf/git/sasview_scripts/trac_migration/out
for f in *.html; do
    if [ "$f" == "*_orig.html" ] ; then
        continue;
    fi
    outfile="${f/.html/.md}"
    command="/usr/local/bin/pandoc -s '$f' -t markdown-simple_tables-multiline_tables-grid_tables --wrap=none --column=999 -o '$outfile'"
    echo "$command"
    output=$(eval "$command")
    echo "$output"
done
if [ -f WikiStart.md ]; then
    mv WikiStart.md Home.md
fi
cp *.md /tmp/sasview.wiki/
cd /tmp/sasview.wiki/
git add .
date=$(date)
git commit -m 'Added MD script'
git push
cd /Users/rhf/git/sasview_scripts/trac_migration

    '''

    os.system(script)

__name__ == '__main__' and main()
