"""Produce and parse the names of duplicity's backup files"""

import re
import dup_time

full_vol_re = re.compile("^duplicity-full\\.(?P<time>.*?)\\.vol(?P<num>[0-9]+)\\.difftar($|\\.)", re.I)
full_manifest_re = re.compile("^duplicity-full\\.(?P<time>.*?)\\.manifest($|\\.)", re.I)

inc_vol_re = re.compile("^duplicity-inc\\.(?P<start_time>.*?)\\.to\\.(?P<end_time>.*?)\\.vol(?P<num>[0-9]+)\\.difftar($|\\.)", re.I)
inc_manifest_re = re.compile("^duplicity-inc\\.(?P<start_time>.*?)\\.to\\.(?P<end_time>.*?)\\.manifest(\\.|$)", re.I)

full_sig_re = re.compile("^duplicity-full-signatures\\.(?P<time>.*?)\\.sigtar(\\.|$)", re.I)
new_sig_re = re.compile("^duplicity-new-signatures\\.(?P<start_time>.*?)\\.to\\.(?P<end_time>.*?)\\.sigtar(\\.|$)", re.I)

def get(type, volume_number = None, manifest = None,
		encrypted = None, gzipped = None):
	"""Return duplicity filename of specified type

	type can be "full", "inc", "full-sig", or "new-sig". volume_number
	can be given with the full and inc types.  If manifest is true the
	filename is of a full or inc manifest file.

	"""
	assert not (encrypted and gzipped)
	if encrypted: suffix = ".gpg"
	elif gzipped: suffix = ".gz"
	else: suffix = ""

	if type == "full-sig" or type == "new-sig":
		assert not volume_number and not manifest
		if type == "full-sig":
			return "duplicity-full-signatures.%s.sigtar%s" % \
				   (dup_time.curtimestr, suffix)
		elif type == "new-sig":
			return "duplicity-new-signatures.%s.to.%s.sigtar%s" % \
				   (dup_time.prevtimestr, dup_time.curtimestr, suffix)
	else:
		assert volume_number or manifest
		assert not (volume_number and manifest)
		if volume_number: vol_string = "vol%d.difftar" % volume_number
		else: vol_string = "manifest"
		if type == "full":
			return "duplicity-full.%s.%s%s" % \
				   (dup_time.curtimestr, vol_string, suffix)
		elif type == "inc":
			return "duplicity-inc.%s.to.%s.%s%s" % \
			  (dup_time.prevtimestr, dup_time.curtimestr, vol_string, suffix)
		else: assert 0


def parse(filename):
	"""Parse duplicity filename, return None or ParseResults object"""
	m1 = full_vol_re.search(filename)
	m2 = full_manifest_re.search(filename)
	if m1 or m2:
		t = dup_time.stringtotime((m1 or m2).group("time"))
		if t:
			if m1: return ParseResults("full", time = t,
									   volume_number = int(m1.group("num")))
			else: return ParseResults("full", time = t, manifest = 1)
		else: return None

	m1 = inc_vol_re.search(filename)
	m2 = inc_manifest_re.search(filename)
	if m1 or m2:
		t1 = dup_time.stringtotime((m1 or m2).group("start_time"))
		t2 = dup_time.stringtotime((m1 or m2).group("end_time"))
		if t1 and t2:
			if m1: return ParseResults("inc", start_time = t1, end_time = t2,
									   volume_number = int(m1.group("num")))
			else: return ParseResults("inc", start_time = t1, end_time = t2,
									  manifest = 1)
		else: return None

	m = full_sig_re.search(filename)
	if m:
		t = dup_time.stringtotime(m.group("time"))
		if t: return ParseResults("full-sig", time = t)
		else: return None
	m = new_sig_re.search(filename)
	if m:
		t1 = dup_time.stringtotime(m.group("start_time"))
		t2 = dup_time.stringtotime(m.group("end_time"))
		if t1 and t2:
			return ParseResults("new-sig", start_time = t1, end_time = t2)
		else: return None

	return None

class ParseResults:
	"""Hold information taken from a duplicity filename"""
	def __init__(self, type, manifest = None, volume_number = None,
				 time = None, start_time = None, end_time = None):
		assert (type == "full-sig" or type == "new-sig" or
				type == "inc" or type == "full")
		self.type = type
		if type == "inc" or type == "full": assert manifest or volume_number
		if type == "inc" or type == "new-sig": assert start_time and end_time
		else: assert time

		self.manifest = manifest
		self.volume_number = volume_number
		self.time = time
		self.start_time, self.end_time = start_time, end_time

		