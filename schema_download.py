"""
Copyright (c) 2011, Anthony Garcia <lagg@lavabit.com>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
"""

import json, urllib2, socket, email.utils, os, sys, subprocess, time
socket.timeout(1)

# Configuration

api_key = None
language = "en"
games = {"Portal 2": 620,
         "Team Fortress 2": 440,
         "Team Fortress 2 Beta": 520,
         "DOTA 2": 570
         }

# GIT_WORKING_TREE is where the files are
tracker_dir = "/home/anthony/NEW_SCHEMA_DOWNLOADER/TEST_DIR/"

# GIT_DIR is the location of the actual git repository
tracker_git_dir = os.path.join(tracker_dir, ".git")

# URL to push to, set to None or empty if you don't want this
tracker_push_url = "file:///home/anthony/NEW_SCHEMA_DOWNLOADER/test_bare.git"

# Change to the location of your git install's binary (probably not needed)
git_binary = "/usr/bin/git"

# Name to commit with
git_name = "TF Wiki"
# Email (will show up in log, set this to something non-existent unless you like spam)
git_email = "noreply@wiki.teamfortress.com"

# Number of seconds to sleep between schema checks, can be less than 1 (e.g. 0.50 for half a second)
schema_check_interval = 10

# End configuration

def process_schema_request(label, request):
    ims = request.get_header("If-modified-since")
    if ims:
        sys.stderr.write("\x1b[1m{0}\x1b[0m younger than \x1b[1m{1}\x1b[0m? ".format(label, ims))
    else:
        sys.stderr.write("Can I have a fresh \x1b[1m{0}\x1b[0m? ".format(label))

    try:
        response = urllib2.urlopen(request)
        schema = response.read()
        schemafile = open(os.path.join(tracker_dir, label), "wb")
        schemafile.write(schema)
        schemafile.close()

        sys.stderr.write("\x1b[32;1mYes\x1b[0m\n")
    except urllib2.HTTPError as err:
        if err.getcode() == 304:
            sys.stderr.write("\x1b[31;1mNo\x1b[0m\n")
        return None

    return response, schema

while True:
    commit_summary = {}
    for k,v in games.iteritems():
        sys.stderr.write("Starting {0} ({1})\n------------\n".format(k, v))

        url = "http://api.steampowered.com/IEconItems_{0}/GetSchema/v0001/?key={1}&language={2}".format(v, api_key, language)

        schema_base_name = "{0} Schema".format(k)
        client_schema_base_name = "{0} Client Schema".format(k)
        schema_path = os.path.join(tracker_dir, schema_base_name)
        client_schema_path = os.path.join(tracker_dir, client_schema_base_name)
        req_headers = {}
        clientreq_headers = {}
        schemadict = None
        schema_lm = ""
        client_schema_lm = ""

        if os.path.exists(schema_path):
            req_headers["If-Modified-Since"] = email.utils.formatdate(os.stat(schema_path).st_mtime, usegmt=True)

        if os.path.exists(client_schema_path):
            clientreq_headers["If-Modified-Since"] = email.utils.formatdate(os.stat(client_schema_path).st_mtime, usegmt=True)

        req = urllib2.Request(url, headers = req_headers)
        res = process_schema_request(schema_base_name, req)
        if res:
            schema_lm = res[0].headers.get("last-modified", "Missing LM")
            schemadict = json.loads(res[1])
        else:
            schemadict = json.load(open(schema_path, "r"))

        if schemadict:
            clientreq = urllib2.Request(schemadict["result"]["items_game_url"], headers = clientreq_headers)

            res = process_schema_request(client_schema_base_name, clientreq)
            if res:
                client_schema_lm = res[0].headers.get("last-modified", "Missing LM")

        if schema_lm: commit_summary[schema_base_name] = schema_lm
        if client_schema_lm: commit_summary[client_schema_base_name] = client_schema_lm

        sys.stderr.write("\nAPI: {0} - Client: {1}\n".format(schema_lm or "No change", client_schema_lm or "No change"))
        sys.stderr.write("\n\n")

    sys.stderr.write("Committing changes\n------------\n")
    summary_top = ", ".join(commit_summary.keys())
    summary_body = ""
    for k, v in commit_summary.items():
        summary_body += "{0}: {1}\n\n".format(k, v)
    if summary_top:
        sys.stderr.write("{0}\n\n{1}\n".format(summary_top, summary_body))

        git_env = {"GIT_DIR": tracker_git_dir, "GIT_WORKING_TREE": tracker_dir,
                   "GIT_AUTHOR_EMAIL": git_email, "GIT_AUTHOR_NAME": git_name,
                   "GIT_COMMITTER_EMAIL": git_email, "GIT_COMMITTER_NAME": git_name}

        # Add all working tree files
        subprocess.Popen([git_binary, 'add', '-A'], env = git_env, cwd = tracker_dir).wait()
        # Commit all (just to make sure)
        subprocess.Popen([git_binary, 'commit', '-a', '-m', summary_top + "\n\n" + summary_body + "\n"],
                         env = git_env, cwd = tracker_dir).wait()
        # Poosh leetle tracker tree (if push URL is set)
        if tracker_push_url:
            subprocess.Popen([git_binary, 'push', '--mirror', tracker_push_url],
                             env = git_env, cwd = tracker_dir).wait()
    else:
        sys.stderr.write("Nothing changed\n")

    sys.stderr.write("\nSleeping for {0} second(s)\n".format(schema_check_interval))
    time.sleep(schema_check_interval)
