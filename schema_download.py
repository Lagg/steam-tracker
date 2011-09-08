import json, urllib2, socket, email.utils, os, sys
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
    schema_lm = "N/A"
    client_schema_lm = "N/A"

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

    sys.stderr.write("\nAPI: {0} - Client: {1}\n".format(schema_lm, client_schema_lm))
    sys.stderr.write("\n\n")
