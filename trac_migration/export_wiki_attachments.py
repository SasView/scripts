#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
import re
import shlex
import time
import urllib.parse
import xmlrpc.client
from base64 import b64decode
from multiprocessing import Pool

import bleach
import pypandoc
from bleach.sanitizer import Cleaner
from bs4 import BeautifulSoup, Comment
from dotenv import load_dotenv
from github import Github
from lxml.html import clean

'''
This script only downloads the attachements of the wiki
'''

OUTPUT_DIRECTORY = "attachs"

# load .env file
load_dotenv()


def main():

    trac_url = os.getenv("TRAC-URL")
    rpc_url = urllib.parse.urljoin(trac_url, 'login/xmlrpc')
    trac = xmlrpc.client.ServerProxy(rpc_url)

    for page in trac.wiki.getAllPages():
        for a in trac.wiki.listAttachments(page):
            print(page, '->', a)
            attachment = trac.wiki.getAttachment(a)

            print(type(attachment))

            filename = f'{a}'.lstrip('/')
            filename = filename.replace('/', '_')
            filename = os.path.join("attachs", filename)

            print("Saving: {}".format(filename))
            dir = os.path.dirname(filename)
            dir and os.makedirs(dir, exist_ok=True)

            with open(filename, 'wb') as f:
                f.write(attachment.data)


__name__ == '__main__' and main()
