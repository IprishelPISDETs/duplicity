# Copyright 2002 Ben Escoto
#
# This file is part of duplicity.
#
# Duplicity is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# Duplicity is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with duplicity; if not, write to the Free Software Foundation,
# Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

"""Provides functions and classes for getting/sending files to destination"""

import os, socket, types, tempfile, time, sys
import log, path, dup_temp, file_naming, atexit
import base64, getpass, xml.dom.minidom, httplib, urllib
import socket, globals, re

socket.setdefaulttimeout(globals.timeout)

class BackendException(Exception): pass
class ParsingException(Exception): pass


def get_backend(url_string):
	"""Return Backend object from url string, or None if not a url string

	url strings are like
	scp://foobar:password@hostname.net:124/usr/local.  If a protocol
	is unsupported a fatal error will be raised.

	"""
	global protocol_class_dict
	try: pu = ParsedUrl(url_string)
	except ParsingException: return None

	try: backend_class = protocol_class_dict[pu.protocol]
	except KeyError: log.FatalError("Unknown protocol '%s'" % (pu.protocol,))
	return backend_class(pu)


class ParsedUrl:
	"""Contains information gleaned from a generic url"""
	protocol = None # set to string like "ftp" indicating protocol
	suffix = None # Set to everything after protocol://

	server = None # First part of suffix (part before '/')
	path = None # Second part of suffix (part after '/')

	host = None # Set to host, if can be extracted
	user = None # Set to user, as in ftp://user@host/whatever
	port = None # Set to port, like scp://host:port/foo

	def __init__(self, url_string):
		"""Create ParsedUrl object, process url_string"""
		self.url_string = url_string
		self.set_protocol_suffix()
		self.set_server_path()
		self.set_host_user_port()

	def bad_url(self, message = None):
		"""Report a bad url, using message if given"""
		if message:
			err_string = "Bad URL string '%s': %s" % (self.url_string, message)
		else: err_string = "Bad URL string '%s'" % (self.url_string,)
		raise ParsingException(err_string)

	def set_protocol_suffix(self):
		"""Parse self.url_string, setting self.protocol and self.suffix"""
		colon_position = self.url_string.find(":")
		if colon_position < 1: self.bad_url("No colon (:) found")
		self.protocol = self.url_string[:colon_position]
		if self.url_string[colon_position+1:colon_position+3] != '//':
			self.bad_url("first colon not followed by '//'")
		self.suffix = self.url_string[colon_position+3:]

	def set_server_path(self):
		"""Set self.server and self.path from self.suffix"""
		comps = self.suffix.split('/')
		assert len(comps) > 0
		self.server = comps[0]
		if len(comps) > 1:
			self.path = '/'.join(comps[1:])

	def set_host_user_port(self):
		"""Set self.host, self.user, and self.port from self.server"""
		if not self.server: return

		# Extract port
		port_comps = self.server.split(":")
		if len(port_comps) >= 2:
			try: self.port = int(port_comps[-1])
			except ValueError: user_host = self.server
			else: user_host = ":".join(port_comps[:-1])
		else: user_host = self.server

		# Set user and host
		user_comps = user_host.split("@")
		if len(user_comps) >= 2:
			self.user = user_comps[0]
			self.host = "@".join(user_comps[1:])
		else: self.host = user_host


class Backend:
	"""Represent a connection to the destination device/computer

	Classes that subclass this should implement the put, get, list,
	and delete methods.

	"""
	def put(self, source_path, remote_filename = None):
		"""Transfer source_path (Path object) to remote_filename (string)

		If remote_filename is None, get the filename from the last
		path component of pathname.

		"""
		if not remote_filename: remote_filename = source_path.get_filename()
		pass

	def get(self, remote_filename, local_path):
		"""Retrieve remote_filename and place in local_path"""
		local_path.setdata()
		pass

	def list(self):
		"""Return list of filenames (strings) present in backend"""
		pass

	def delete(self, filename_list):
		"""Delete each filename in filename_list, in order if possible"""
		pass

	def munge_password(self, commandline):
		try:
			password = os.environ['FTP_PASSWORD']
			return re.sub(re.escape(password), "???", commandline)
		except:
			return commandline

	def run_command(self, commandline):
		"""Run given commandline with logging and error detection"""
		private = self.munge_password(commandline)
		log.Log("Running '%s'" % private, 5)
		if os.system(commandline):
			raise BackendException("Error running '%s'" % private)

	def run_command_persist(self, commandline):
		"""Run given commandline with logging and error detection
		repeating it several times if it fails"""
		for n in range(1, globals.num_retries+1):
			private = self.munge_password(commandline)
			log.Log("Running '%s' (attempt #%d)" % (private, n), 5)
			if not os.system(commandline):
				return
			log.Log("Running '%s' failed (attempt #%d)" % (private, n), 1)
			time.sleep(30)
		log.Log("Giving up trying to execute '%s' after %d attempts" % (private, globals.num_retries), 1)
		raise BackendException("Error running '%s'" % private)

	def popen(self, commandline):
		"""Run command and return stdout results"""
		private = self.munge_password(commandline)
		log.Log("Reading results of '%s'" % private, 5)
		fout = os.popen(commandline)
		results = fout.read()
		if fout.close():
			raise BackendException("Error running '%s'" % private)
		return results

	def popen_persist(self, commandline):
		"""Run command and return stdout results, repeating on failure"""
		for n in range(1, globals.num_retries+1):
			private = self.munge_password(commandline)
			log.Log("Reading results of '%s'" % private, 5)
			fout = os.popen(commandline)
			results = fout.read()
			if not fout.close():
				return results
			log.Log("Running '%s' failed (attempt #%d)" % (private, n), 1)
			time.sleep(30)
		log.Log("Giving up trying to execute '%s' after %d attempts" % (private, globals.num_retries), 1)
		raise BackendException("Error running '%s'" % private)

	def get_fileobj_read(self, filename, parseresults = None):
		"""Return fileobject opened for reading of filename on backend

		The file will be downloaded first into a temp file.  When the
		returned fileobj is closed, the temp file will be deleted.

		"""
		if not parseresults:
			parseresults = file_naming.parse(filename)
			assert parseresults, "Filename not correctly parsed"
		tdp = dup_temp.new_tempduppath(parseresults)
		self.get(filename, tdp)
		tdp.setdata()
		return tdp.filtered_open_with_delete("rb")

	def get_fileobj_write(self, filename, parseresults = None,
						  sizelist = None):
		"""Return fileobj opened for writing, write to backend on close

		The file will be encoded as specified in parseresults (or as
		read from the filename), and stored in a temp file until it
		can be copied over and deleted.

		If sizelist is not None, it should be set to an empty list.
		The number of bytes will be inserted into the list.

		"""
		if not parseresults:
			parseresults = file_naming.parse(filename)
			assert parseresults, "Filename %s not correctly parsed" % filename
		tdp = dup_temp.new_tempduppath(parseresults)

		def close_file_hook():
			"""This is called when returned fileobj is closed"""
			self.put(tdp, filename)
			if sizelist is not None:
				tdp.setdata()
				sizelist.append(tdp.getsize())
			tdp.delete()

		fh = dup_temp.FileobjHooked(tdp.filtered_open("wb"))
		fh.addhook(close_file_hook)
		return fh

	def get_data(self, filename, parseresults = None):
		"""Retrieve a file from backend, process it, return contents"""
		fin = self.get_fileobj_read(filename, parseresults)
		buf = fin.read()
		assert not fin.close()
		return buf

	def put_data(self, buffer, filename, parseresults = None):
		"""Put buffer into filename on backend after processing"""
		fout = self.get_fileobj_write(filename, parseresults)
		fout.write(buffer)
		assert not fout.close()

	def close(self):
		"""This is called when a connection is no longer needed"""
		pass


class LocalBackend(Backend):
	"""Use this backend when saving to local disk

	Urls look like file://testfiles/output.  Relative to root can be
	gotten with extra slash (file:///usr/local).

	"""
	def __init__(self, parsed_url):
		self.remote_pathdir = path.Path(parsed_url.suffix)

	def put(self, source_path, remote_filename = None, rename = None):
		"""If rename is set, try that first, copying if doesn't work"""
		if not remote_filename: remote_filename = source_path.get_filename()
		target_path = self.remote_pathdir.append(remote_filename)
		log.Log("Writing %s" % target_path.name, 6)
		if rename:
			try: source_path.rename(target_path)
			except OSError: pass
			else: return
		target_path.writefileobj(source_path.open("rb"))

	def get(self, filename, local_path):
		"""Get file and put in local_path (Path object)"""
		source_path = self.remote_pathdir.append(filename)
		local_path.writefileobj(source_path.open("rb"))

	def list(self):
		"""List files in that directory"""
		return self.remote_pathdir.listdir()

	def delete(self, filename_list):
		"""Delete all files in filename list"""
		assert type(filename_list) is not types.StringType
		try:
			for filename in filename_list:
				self.remote_pathdir.append(filename).delete()
		except OSError, e: raise BackendException(str(e))


# The following can be redefined to use different shell commands from
# ssh or scp or to add more arguments.	However, the replacements must
# have the same syntax.  Also these strings will be executed by the
# shell, so shouldn't have strange characters in them.
ssh_command = "ssh"
scp_command = "scp"
sftp_command = "sftp"

class scpBackend(Backend):
	"""This backend copies files using scp.  List not supported"""
	def __init__(self, parsed_url):
		"""scpBackend initializer"""
		self.host_string = parsed_url.server # of form user@hostname:port
		self.remote_dir = parsed_url.path # can be empty string
		if parsed_url.port: self.port_string = parsed_url.port
		else: self.port_string = 22
		if self.remote_dir: self.remote_prefix = self.remote_dir + "/"
		else: self.remote_prefix = ""

	def put(self, source_path, remote_filename = None):
		"""Use scp to copy source_dir/filename to remote computer"""
		if not remote_filename: remote_filename = source_path.get_filename()
		commandline = "%s -P %s %s %s:%s%s" % \
					  (scp_command, self.port_string, source_path.name, self.host_string,
					   self.remote_prefix, remote_filename)
		self.run_command_persist(commandline)

	def get(self, remote_filename, local_path):
		"""Use scp to get a remote file"""
		commandline = "%s -P %s %s:%s%s %s" % \
					  (scp_command, self.port_string, self.host_string, self.remote_prefix,
					   remote_filename, local_path.name)
		self.run_command_persist(commandline)
		local_path.setdata()
		if not local_path.exists():
			raise BackendException("File %s not found" % local_path.name)

	def list(self):
		"""List files available for scp

		Note that this command can get confused when dealing with
		files with newlines in them, as the embedded newlines cannot
		be distinguished from the file boundaries.

		"""
		commandline = ("printf 'cd %s\nls -1' | %s -oPort=%s -b - %s" %
					   (self.remote_dir, sftp_command, self.port_string, self.host_string))
		l = self.popen(commandline).split('\n')[2:] # omit sftp prompts
		return filter(lambda x: x, l)

	def delete(self, filename_list):
		"""Runs ssh rm to delete files.  Files must not require quoting"""
		assert len(filename_list) > 0
		pathlist = map(lambda fn: self.remote_prefix + fn, filename_list)
		del_prefix = "echo 'rm "
		del_postfix = ("' | %s -oPort=%s -b - %s 1>/dev/null" %
					   (sftp_command, self.port_string, self.host_string))
		for fn in filename_list:
			commandline = del_prefix + self.remote_prefix + fn + del_postfix
			self.run_command(commandline)


class sftpBackend(Backend):
	"""This backend uses sftp to perform file operations"""
	pass # Do this later


class ftpBackend(Backend):
	"""Connect to remote store using File Transfer Protocol"""
	def __init__(self, parsed_url):
		self.url_string = parsed_url.url_string
		if self.url_string[-1] != '/':
			self.url_string += '/'
		self.password = self.get_password()
		self.tempfile, self.tempname = tempfile.mkstemp()
		if globals.ftp_connection == 'regular':
			self.conn_opt = '-E'
		else:
			self.conn_opt = '-F'
		self.flags = "%s -t %s -u '%s' -p '%s'" % \
					 (self.conn_opt, globals.timeout, parsed_url.user, self.password)

	def get_password(self):
		"""Get ftp password using environment if possible"""
		try: password = os.environ['FTP_PASSWORD']
		except KeyError:
			password = getpass.getpass("Password for '%s': " % self.url_string)
			os.environ['FTP_PASSWORD'] = password
		return password

	def put(self, source_path, remote_filename = None):
		"""Transfer source_path to remote_filename"""
		pu = ParsedUrl(self.url_string)
		remote_path = os.path.join (pu.path, remote_filename).rstrip()
		commandline = "ncftpput %s -V -c '%s' '%s' < '%s'" % \
					  (self.flags, pu.host, remote_path, source_path.name)
		self.run_command_persist(commandline)

	def get(self, remote_filename, local_path):
		"""Get remote filename, saving it to local_path"""
		pu = ParsedUrl(self.url_string)
		remote_path = os.path.join(pu.path, remote_filename).rstrip()
		commandline = "ncftpget %s -V -c '%s' '%s' > '%s'" % \
					  (self.flags, pu.host, remote_path, local_path.name)
		self.run_command_persist(commandline)
		local_path.setdata()

	def list(self):
		"""List files in directory"""
		commandline = "ncftpls %s '%s'" % \
					  (self.flags, self.url_string)
		l = self.popen_persist(commandline).split('\n')
		return filter(lambda x: x, l)

	def delete(self, filename_list):
		"""Delete files in filename_list"""
		pu = ParsedUrl(self.url_string)
		for filename in filename_list:
			commandline = "ncftpls %s -X 'DELE /%s%s' '%s' >& /dev/null" % \
						  (self.flags, pu.path, filename, self.url_string)
			self.run_command_persist(commandline)


class rsyncBackend(Backend):
	"""Connect to remote store using rsync

	rsync backend contributed by Sebastian Wilhelmi <seppi@seppi.de>

	"""
	def __init__(self, parsed_url):
		"""rsyncBackend initializer"""
		self.url_string = "%s:%s" % (parsed_url.server, parsed_url.path)
		if self.url_string[-1] != '/':
			self.url_string += '/'

	def put(self, source_path, remote_filename = None):
		"""Use rsync to copy source_dir/filename to remote computer"""
		if not remote_filename: remote_filename = source_path.get_filename()
		remote_path = os.path.join (self.url_string, remote_filename)
		commandline = "rsync %s %s" % (source_path.name, remote_path)
		self.run_command(commandline)

	def get(self, remote_filename, local_path):
		"""Use rsync to get a remote file"""
		remote_path = os.path.join (self.url_string, remote_filename)
		commandline = "rsync %s %s" % (remote_path, local_path.name)
		self.run_command(commandline)
		local_path.setdata()
		if not local_path.exists():
			raise BackendException("File %s not found" % local_path.name)

	def list(self):
		"""List files"""
		def split (str):
			line = str.split ()
			if len (line) > 4 and line[4] != '.':
				return line[4]
			else:
				return None
		commandline = "rsync %s" % self.url_string
		return filter (lambda x: x, map (split, self.popen(commandline).split('\n')))

	def delete(self, filename_list):
		"""Delete files."""
		delete_list = filename_list
		dont_delete_list = []
		for file in self.list ():
			if file in delete_list:
				delete_list.remove (file)
			else:
				dont_delete_list.append (file)
		if len (delete_list) > 0:
			raise BackendException("Files %s not found" % str (delete_list))

		dir = tempfile.mktemp ()
		exclude_name = tempfile.mktemp ()
		exclude = open (exclude_name, 'w')
		to_delete = [exclude_name]
		os.mkdir (dir)
		for file in dont_delete_list:
			path = os.path.join (dir, file)
			to_delete.append (path)
			f = open (path, 'w')
			f.close ()
			print >>exclude, file
		exclude.close ()
		commandline = ("rsync --recursive --delete --exclude-from=%s %s/ %s" %
					   (exclude_name, dir, self.url_string))
		self.run_command(commandline)
		for file in to_delete:
			os.unlink (file)
		os.rmdir (dir)


class BotoBackend(Backend):
	"""
	Backend for Amazon's Simple Storage System, (aka Amazon S3), though
	the use of the boto module, (http://code.google.com/p/boto/).

	To make use of this backend you must export the environment variables
	AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY with your Amazon Web 
	Services key id and secret respectively.
	"""

	def __init__(self, parsed_url):
		try:
			from boto.s3.connection import S3Connection
			from boto.s3.key import Key
		except ImportError:
			raise BackendException("This backend requires the boto library, " \
				"(http://code.google.com/p/boto/).")

		self.key_class = Key

		self.conn = S3Connection()
		self.bucket_name = parsed_url.suffix
		self.bucket = self.conn.create_bucket(self.bucket_name)

		if not (os.environ.has_key('AWS_ACCESS_KEY_ID') and 
				os.environ.has_key('AWS_SECRET_ACCESS_KEY')):
			raise BackendException("The AWS_ACCESS_KEY_ID and " \
				"AWS_SECRET_ACCESS_KEY environment variables are not set.")

		if '/' in self.bucket_name:
			raise BackendException("Invalid bucket specification.")

	def put(self, source_path, remote_filename=None):
		if not remote_filename:
			remote_filename = source_path.get_filename()
		log.Log("Uploading %s to Amazon S3" % remote_filename, 5)
		key = self.key_class(self.bucket)
		key.key = remote_filename
		key.set_contents_from_filename(source_path.name, 
				{'Content-Type': 'application/octet-stream'})
	
	def get(self, remote_filename, local_path):
		log.Log("Downloading %s from Amazon S3" % remote_filename, 5)
		key = self.key_class(self.bucket)
		key.key = remote_filename
		key.get_contents_to_filename(local_path.name)
		local_path.setdata()

	def list(self):
		filename_list = [k.key for k in self.bucket.get_all_keys()]
		log.Log("Files in bucket:\n%s" % string.join(filename_list, '\n'), 9)
		return filename_list

	def delete(self, filename_list):
		for filename in filename_list:
			self.bucket.delete_key(filename)


class webdavBackend(Backend):
	"""Backend for accessing a WebDAV repository.
	
	webdav backend contributed in 2006 by Jesper Zedlitz <jesper@zedlitz.de>
	"""
	listbody = """\
<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:allprop/>
</D:propfind>

"""
	
	"""Connect to remote store using WebDAV Protocol"""
	def __init__(self, parsed_url):
		self.headers = {}
		self.parsed_url = parsed_url
		
		if parsed_url.path:
			self.directory = '/' + parsed_url.path.rstrip('/') + '/'
		else:
			self.directory = '/'
		
		if self.directory == '//':
			self.directory = '/'
		log.Log("Using WebDAV host %s" % (parsed_url.host,), 5)
		log.Log("Using WebDAV directory %s" % (self.directory,), 5)
		
		try: password = os.environ['FTP_PASSWORD']
		except KeyError:
			password = getpass.getpass('Password for '+parsed_url.user+'@'+parsed_url.host+': ')

		self.conn = httplib.HTTPConnection(parsed_url.host)
		self.headers['Authorization'] = 'Basic ' + base64.encodestring(parsed_url.user+':'+ password).strip()
		
		# check password by connection to the server
		self.conn.request("OPTIONS", self.directory, None, self.headers)
		response = self.conn.getresponse()
		response.read()
		if response.status !=  200:
			raise BackendException((response.status, response.reason))

	def _getText(self,nodelist):
		rc = ""
		for node in nodelist:
			if node.nodeType == node.TEXT_NODE:
				rc = rc + node.data
		return rc

	def close(self):
		self.conn.close()
		
	def list(self):
		"""List files in directory"""
		log.Log("Listing directory %s on WebDAV server" % (self.directory,), 5)
		self.headers['Depth'] = "1"
		self.conn.request("PROPFIND", self.directory, self.listbody, self.headers)
		del self.headers['Depth']
		response = self.conn.getresponse()
		if response.status != 207:
			raise BackendException((response.status, response.reason))

		document = response.read()
		print document
		dom = xml.dom.minidom.parseString(document)

		result = []
		for href in dom.getElementsByTagName('D:href'):
			filename = urllib.unquote(self._getText(href.childNodes).strip())
			if filename.startswith(self.directory):
				filename = filename.replace(self.directory,'',1)
				result.append(filename)
		return result

	def get(self, remote_filename, local_path):
		"""Get remote filename, saving it to local_path"""
		url = self.directory + remote_filename
		log.Log("Retrieving %s from FTP server" % (url ,), 5)
		target_file = local_path.open("wb")
		self.conn.request("GET", url, None, self.headers)
		response = self.conn.getresponse()		
		if response.status != 200:
			raise BackendException((response.status, response.reason))
		target_file.write(response.read())
		assert not target_file.close()
		local_path.setdata()

	def put(self, source_path, remote_filename = None):
		"""Transfer source_path to remote_filename"""
		if not remote_filename: 
			remote_filename = source_path.get_filename()
		url = self.directory + remote_filename
		source_file = source_path.open("rb")
		log.Log("Saving %s on WebDAV server" % (url,), 5)
		self.conn.request("PUT", url, source_file.read(), self.headers)
		response = self.conn.getresponse()
		if response.status != 201:
			raise BackendException((response.status, response.reason))
		response.read()
		assert not source_file.close()

	def delete(self, filename_list):
		"""Delete files in filename_list"""
		for filename in filename_list:
			url = self.directory + filename
			log.Log("Deleting %s from WebDAV server" % (url,), 5)
			self.conn.request("DELETE", url, None, self.headers)
			response = self.conn.getresponse()
			if response.status != 204:
				raise BackendException((response.status, response.reason))
			response.read()


hsi_command = "hsi"
class hsiBackend(Backend):
	def __init__(self, parsed_url):
		self.host_string = parsed_url.server
		self.remote_dir = parsed_url.path
		if self.remote_dir: self.remote_prefix = self.remote_dir + "/"
		else: self.remote_prefix = ""

	def put(self, source_path, remote_filename = None):
		if not remote_filename: remote_filename = source_path.get_filename()
		commandline = '%s "put %s : %s%s"' % (hsi_command,source_path.name,self.remote_prefix,remote_filename)
		try:
			self.run_command(commandline)
		except:
			print commandline

	def get(self, remote_filename, local_path):
		commandline = '%s "get %s : %s%s"' % (hsi_command, local_path.name, self.remote_prefix, remote_filename)
		self.run_command(commandline)
		local_path.setdata()
		if not local_path.exists():
			raise BackendException("File %s not found" % local_path.name)

	def list(self):
		commandline = '%s "ls -l %s"' % (hsi_command, self.remote_dir)
		l = os.popen3(commandline)[2].readlines()[3:]
		for i in range(0,len(l)):
			l[i] = l[i].split()[-1]
		print filter(lambda x: x, l)
		return filter(lambda x: x, l)

	def delete(self, filename_list):
		assert len(filename_ist) > 0
		pathlist = map(lambda fn: self.remote_prefix + fn, filename_list)
		for fn in filename_list:
			commandline = '%s "rm %s%s"' % (hsi_command, self.remote_prefix, fn)
			self.run_command(commandline)


# Dictionary relating protocol strings to backend_object classes.
protocol_class_dict = {"scp": scpBackend,
					   "ssh": scpBackend,
					   "file": LocalBackend,
					   "ftp": ftpBackend,
					   "hsi": hsiBackend,
					   "rsync": rsyncBackend,
					   "s3+http": BotoBackend,
					   "webdav": webdavBackend}