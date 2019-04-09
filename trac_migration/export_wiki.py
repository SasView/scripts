#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
import re
import shlex
import subprocess
import tempfile
import time
import urllib.parse
import xmlrpc.client
from datetime import datetime, timezone
from multiprocessing import Pool

import bleach
from bleach.sanitizer import Cleaner
from bs4 import BeautifulSoup, Comment
from dotenv import load_dotenv
from github import Github
from lxml.html import clean

'''
Migrates trac wiki to github
'''


NUMBER_OF_CORES = 7

OUTPUT_DIRECTORY = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "out")

DEFAULT_GITHUB_USERNAME = "sasview-bot"

SCRIPT = '''#!/bin/bash
cd {out_directory}
echo {out_directory}
for f in *.html; do
    if [ $f = *_orig.html ] ; then
        echo "Skipping file: $f"
        continue;
    fi
    outfile="${{f/.html/.md}}"
    command="`which pandoc` -s '$f' -t markdown-simple_tables-multiline_tables-grid_tables --wrap=none --column=999 -o '$outfile'"
    echo "$command"
    output=$(eval "$command")
    echo "$output"
done
if [ -f WikiStart.md ]; then
    mv WikiStart.md Home.md
fi
rsync -av --progress *.md {wiki_repo} --exclude "*_orig.*"
cd {wiki_repo}
git add .
git commit -m "Added MD script {date_now}"
git push
cd {cwd}
'''.format(**dict(
    out_directory=OUTPUT_DIRECTORY,
    date_now=datetime.now(timezone.utc).isoformat(),
    wiki_repo="/home/rhf/git/sasview_wiki",
    cwd=os.path.dirname(os.path.realpath(__file__)),
))

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
        for i in github_repo.get_issues(state="all"):
            ticket_number = re.findall('.*\(Trac #(\d+)\)', i.title)
            if ticket_number:
                issues_map[int(ticket_number[0])] = (repo_name, i.number)
    return issues_map


issues_map = update_issues_map()


def update_ticket_link_to_gh_issues(text):
    '''
    replaces: http://trac.sasview.org/ticket/{###}
    with: Sasview/{REPO}#{####} 
    '''

    # Pattern to find in the text
    pattern = r"<a.*href=[\"'](https*:\/\/trac\.sasview\.org)?\/ticket\/(\d+)\/*[\"'].*>(.+)<\/a>"

    # Regex here!!!
    def replacer(match):
        ''' To replace in the matched groups. Only way that I found....
        group 2 = ticket number
        group 3 = Description of the link
        '''

        repo_name, issue_number = issues_map.get(
            int(match.group(2)), (None, None))
        # To every found <a> this is the substitution link
        if match.group(3).startswith('#') or "trac.sasview.org" in match.group(3):
            to_replace = r'<a href="/SasView/{0}/issues/{1}">SasView/{0}#{1}</a>'.format(
                repo_name, issue_number)
        else:
            to_replace = r'<a href="/SasView/{0}/issues/{1}">{2}</a>'.format(
                repo_name, issue_number, match.group(3))
        return to_replace

    text_corrected = re.sub(pattern, replacer, text)
    return text_corrected


def sanitise_html(content):
    attributes = bleach.sanitizer.ALLOWED_ATTRIBUTES
    attributes.update({
        'img': ['alt', 'src'],
    })
    cleaner = Cleaner(
        tags=bleach.sanitizer.ALLOWED_TAGS+[
            'pre', 'table', 'tr', 'td', 'th', 'tt', 'dl', 'dt', 'dd',
            "a", "h1", "h2", "h3", "strong", "em", "p", "ul", "ol",
            "li", "br", "sub", "sup", "hr", "img"],
        attributes=attributes,
        strip=True, strip_comments=True)
    return cleaner.clean(content)


def process_single_file(page):

    # TODO remove this temp hack
    # matches = [
    #     # "ListofModels",
    #     # 'WikiStart',
    #     # "CondaDevSetup",
    #     "Tutorials/KU/SAS",
    #     # "TutorialsTNG",
    # ]
    # if not any(s in page for s in matches):
    #     return

    filename = f'{page}.html'.lstrip('/')
    filename = filename.replace('/', '_')
    filename = os.path.join(OUTPUT_DIRECTORY, filename)

    print("Saving: {}".format(filename))
    dir = os.path.dirname(filename)
    dir and os.makedirs(dir, exist_ok=True)

    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)
    # doc is a string
    doc = trac.wiki.getPageHTML(page)

    # Write a copy of the original
    with open(filename.replace(".html", "_orig.html"), 'w') as f2:
        f2.write(doc)

    doc = update_ticket_link_to_gh_issues(doc)

    doc = sanitise_html(doc)

    # Update the links to other pages
    def replacer(match):
        ''' To replace in the matched groups. Only way that I found....'''
        return 'href="{}{}"'.format(
            match.group(1),
            match.group(2) if match.group(2) is not None else ''
        ).replace('/', '_')
    doc = re.sub(
        r'''href=\"http://trac\.sasview\.org/wiki/([a-zA-Z0-9/]+)(#[a-zA-Z0-9]+)?\"''',
        replacer, doc)

    # Update the links to attachments
    def replace_attachments(match):
        ''' To replace in the matched groups. Only way that I found....'''
        filename = match.group(3).replace('/', '_')
        return '{}="attachments/{}"'.format(match.group(1), filename)
    doc = re.sub(
        r'(href|src)=\"https?://trac\.sasview\.org\/(raw-attachment|attachment)\/wiki\/([a-zA-Z0-9\/\._]+)\"',
        replace_attachments, doc)

    soup = BeautifulSoup(doc, features="lxml")
    doc = soup.prettify()

    with open(filename, 'w') as f:
        f.write(doc)


def main():
    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)

    pages = list(trac.wiki.getAllPages())
    with Pool(NUMBER_OF_CORES) as p:
        p.map(process_single_file, pages)

    # script to convert html to md and update github
    with tempfile.NamedTemporaryFile() as fp:
        fp.write(SCRIPT.encode('ascii'))
        fp.flush()
        proc = subprocess.run(['/bin/bash', fp.name],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(proc.stdout.decode('utf-8'))


__name__ == '__main__' and main()
