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

import json, urllib2, socket, email.utils, os, sys, subprocess, time, logging

# Configuration

api_key = None
language = "en"
fetch_timeout = 5
games = {"Portal 2": 620,
         "Team Fortress 2": 440,
         "Team Fortress 2 Beta": 520,
         "DOTA 2": 570,
         "DOTA 2 Alt 1": 816
         }

# GIT_WORKING_TREE is where the files are
tracker_dir = os.path.join(os.getcwd(), "schema-tracking/")

# GIT_DIR is the location of the actual git repository
tracker_git_dir = os.path.join(tracker_dir, ".git")

# URL to push to, set to None or empty if you don't want this
tracker_push_url = os.path.join(os.getcwd(), "schema-tracking-bare.git")

# Change to the location of your git install's binary (probably not needed)
git_binary = "/usr/bin/git"

# Name to commit with
git_name = "TF Wiki"
# Email (will show up in log, set this to something non-existent unless you like spam)
git_email = "noreply@wiki.teamfortress.com"

# Number of seconds to sleep between schema checks, can be less than 1 (e.g. 0.50 for half a second)
schema_check_interval = 10

# End configuration

# Different handlers can be set for this, maybe for log display in IRC at some point
log = logging.getLogger("schema-daemon")
log.setLevel(logging.INFO)

log_handler = logging.StreamHandler()
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter("%(levelname)s:\t %(message)s"))

log.addHandler(log_handler)

def process_schema_request(label, request):
    ims = request.get_header("If-modified-since")

    try:
        log.info("Checking for {0} younger than {1}".format(label, ims or "now"))
        response = urllib2.urlopen(request, timeout = fetch_timeout)
        schema = response.read()
        schemafile = open(os.path.join(tracker_dir, label), "wb")
        schemafile.write(schema)
        schemafile.close()
    except urllib2.HTTPError as err:
        code = err.getcode()
        if code != 304:
            log.error("{0} server returned HTTP {1}".format(label, err.getcode()))
        return None
    except urllib2.URLError as err:
        log.error(err)
        return None
    except Exception as err:
        log.error("Unknown error: {0}".format(str(err)))
        return None

    return response, schema

while True:
    commit_summary = {}
    for k,v in games.iteritems():
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
	try:
            if res:
                schema_lm = res[0].headers.get("last-modified", "Missing LM")
                schemadict = json.loads(res[1])
            elif os.path.exists(schema_path):
                schemadict = json.load(open(schema_path, "r"))
	except Exception as e:
            log.error("Schema load error: {0}".format(e))

        if schemadict:
            clientreq = urllib2.Request(schemadict["result"]["items_game_url"], headers = clientreq_headers)

            res = process_schema_request(client_schema_base_name, clientreq)
            if res:
                client_schema_lm = res[0].headers.get("last-modified", "Missing LM")

        if schema_lm:
            commit_summary[schema_base_name] = schema_lm
            schema_lm_ts = time.mktime(email.utils.parsedate(schema_lm))
            os.utime(schema_path, (schema_lm_ts, schema_lm_ts))
            log.info("Server returned {0} - Last change: {1}".format(schema_base_name, schema_lm))

        if client_schema_lm:
            commit_summary[client_schema_base_name] = client_schema_lm
            client_schema_lm_ts = time.mktime(email.utils.parsedate(client_schema_lm))
            os.utime(client_schema_path, (client_schema_lm_ts, client_schema_lm_ts))
            log.info("Server returned {0} - Last change: {1}".format(client_schema_base_name, client_schema_lm))

    summary_top = ", ".join(commit_summary.keys())
    summary_body = ""
    for k, v in commit_summary.items():
        summary_body += "{0}: {1}\n\n".format(k, v)
    if summary_top:
        bitbucket = None
        if log.getEffectiveLevel() > logging.DEBUG: bitbucket = open(os.devnull, "w")

        log.info("Preparing commit...")
        log.debug("{0}\n\n{1}\n".format(summary_top, summary_body))

        git_env = {"GIT_DIR": tracker_git_dir, "GIT_WORKING_TREE": tracker_dir,
                   "GIT_AUTHOR_EMAIL": git_email, "GIT_AUTHOR_NAME": git_name,
                   "GIT_COMMITTER_EMAIL": git_email, "GIT_COMMITTER_NAME": git_name}

        # Add all working tree files
        subprocess.Popen([git_binary, "add", "-A"], env = git_env, cwd = tracker_dir, stdout = bitbucket).wait()
        # Commit all (just to make sure)
        subprocess.Popen([git_binary, "commit", "-a", "-m", summary_top + "\n\n" + summary_body + "\n"],
                         env = git_env, cwd = tracker_dir, stdout = bitbucket).wait()
        # Poosh leetle tracker tree (if push URL is set)
        if tracker_push_url:
            subprocess.Popen([git_binary, "push", "--porcelain", "--mirror", tracker_push_url],
                             env = git_env, cwd = tracker_dir, stdout = bitbucket).wait()
    else:
        log.info("Nothing changed")

    log.info("Sleeping for {0} second(s)".format(schema_check_interval))
    time.sleep(schema_check_interval)
