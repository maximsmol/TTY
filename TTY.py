import sublime, sublime_plugin
import os, threading, traceback
import subprocess, pty, shlex, signal, fcntl

# todo: add kill functionality
# todo: add discard functionality
# todo: add start/stop functionality
# todo: add dsusp functionality
# todo: add rprnt functionality
# todo: add werase functionality
# todo: add lnext functionality

ST2 = int(sublime.version()) < 3000
DEBUG = True

def st_msg(*args):
	sublime.message_dialog('[TTY]\n'+' '.join([str(a) for a in args]))

def st_debug(*args):
	if not DEBUG:
		return

	print('[TTY]: '+' '.join([str(a) for a in args]))

def st_error(*args):
	msg = '[TTY]\nERROR:\n'
	msg += ' '.join([str(a) for a in args])
	msg += '\nSend this error message to the developer or open a github issue'
	msg += '\nmaximsmol <maximsmol@gmail.com> https://github.com/maximsmol/TTY'

	if DEBUG:
		print(msg)
	else:
		sublime.error_message(msg)

def st_assert(*args):
	for arg in args:
		if arg is None or not arg:
			st_error('Assertion failed: '+str(arg))
			raise Exception('Assertion failed: '+str(arg))

def st_exception_in(*args):
	st_error(' '.join([str(a) for a in args])+'\n\n'+traceback.format_exc())

def term_death_message(view, process):
	ret = process.poll()
	data = [view.name(), '('+str(view.buffer_id())+')', 'pid:', str(process.pid), 'returned:', str(ret) if ret else 'unknown']
	return ' '.join(data)

ESC = '\x1B'
CSI = ESC+'['

class Codes:
	recognized_but_unknown = -2
	unknown = -1
	cursor_save_position = 0
	cursor_restore_position = 1

	cursor_hide = 2
	cursor_show = 3

	cursor_move_up = 4
	cursor_move_down = 5
	cursor_move_forward = 6
	cursor_move_backward = 7

	cursor_move_next_line = 8
	cursor_move_previous_line = 9

	cursor_move_absolute_horizontal = 10
	cursor_move_absolute_both = 11

	erase_all = 12
	erase_line = 13

	scroll_up = 14
	scroll_down = 15

	cursor_move_absolute_both_alternative = 16
	set_graphics_options = 17

class SGROptions:
	reset = 0
	bold = 1
	faint = 2
	italic = 3
	underline = 4
	blink_slow = 5
	blink_rapid = 6
	negative = 7
	conceal = 8 # todo: wtf
	strike_out = 9

	font = 10
	font_default = 10
	font_alternative_1 = 11
	font_alternative_2 = 12
	font_alternative_3 = 13
	font_alternative_4 = 14
	font_alternative_5 = 15
	font_alternative_6 = 16
	font_alternative_7 = 17
	font_alternative_8 = 18
	font_alternative_9 = 19
	franktur = 20 # todo: wtf
	bold_off_or_underline_double = 21 # todo: which is standard?
	not_bold_or_faint = 22
	not_italic_or_franktur = 23
	no_underline = 24
	no_blink = 25
	reversed = 26
	positive = 27
	reveal = 28
	no_strike_out = 29
	color_foreground_black   = 30
	color_foreground_red     = 31
	color_foreground_green   = 32
	color_foreground_yellow  = 33
	color_foreground_blue    = 34
	color_foreground_magenta = 35
	color_foreground_cyan    = 36
	color_foreground_white   = 37
	color_foreground_extended = 38
	color_foreground_default = 39
	color_background_black   = 40
	color_background_red     = 41
	color_background_green   = 42
	color_background_yellow  = 43
	color_background_blue    = 44
	color_background_magenta = 45
	color_background_cyan    = 46
	color_background_white   = 47
	color_background_extended = 48
	color_background_default = 49
	reserved_50 = 50 # todo: wtf
	frame = 51
	encircle = 52
	overline = 53
	no_frame_or_encircle = 54
	no_overline = 55
	reserved_56 = 56
	reserved_57 = 57
	reserved_58 = 58
	reserved_59 = 59
	ideogram_underline_or_right_side_line = 60 # todo: wtf; which is standard?
	ideogram_underline_or_right_side_line_double = 61 # todo: wtf; which is standard?
	ideogram_overline_or_left_side_line = 62 # todo: wtf; which is standard?
	ideogram_overline_or_left_side_line_double = 63 # todo: wtf; which is standard?
	ideogram_stress_marking = 64 # todo: wtf
	ideogram_off = 65

class DummyDataContainer:
	def __getattr__(_, __):
		return None

	def __bool__(_):
		return False
DUMMY = DummyDataContainer()

class TerminalDataContainer:
	def __init__(self, process, cmd_instance):
		st_assert(process)
		st_assert(process.returncode is None)
		st_assert(cmd_instance)

		data = {
			'process': process,
			'cmd_instance': cmd_instance
		}
		object.__setattr__(self, 'data', data)

	@staticmethod
	def _can_use_name(name):
		return name == 'process' or name == 'cmd_instance'

	def _assert_name(name):
		st_assert(TerminalDataContainer._can_use_name(name))

	def __getattr__(self, name):
		TerminalDataContainer._assert_name(name)

		data = object.__getattribute__(self, 'data')
		return data[name] if name in data else None

	def __setattr__(self, name, value):
		TerminalDataContainer._assert_name(name)

		self.data[name] = value

	def __delattr__(self, name):
		st_assert('Don\'t delete terminal data fields')

class TerminalContainer:
	def __init__(self):
		object.__setattr__(self, 'data', {})

	def _get(self, id):
		return self.data[id] if id in self.data else DUMMY

	def _del(self, id):
		st_assert(id in self.data)

		del self.data[id]

	@staticmethod
	def _key_from_view(view):
		return view.buffer_id()

	def del_view(self, view):
		self._del(TerminalContainer._key_from_view(view))
		st_debug('Terminal deleted. Terminals left:', len(self.data))

	def try_del_view(self, view):
		if view.buffer_id() in self.data:
			self.del_view(view)

	def get_data_of(self, view):
		return self._get(TerminalContainer._key_from_view(view))

	def add_view(self, view, process, cmd_instance):
		self.data[TerminalContainer._key_from_view(view)] = TerminalDataContainer(process, cmd_instance)

terminals = TerminalContainer()

class TtyHelperReplaceCommand(sublime_plugin.TextCommand):
	def run(self, edit, start, end, new_text):
		self.view.replace(edit, sublime.Region(start, end), new_text)

def replace_view_region(view, start, end, new_text):
	view.run_command('tty_helper_replace', {'start': start, 'end': end, 'new_text': new_text})

class CursorPos:
	def __init__(self, view, row, col):
		self.view = view

		self.row_data = row
		self.col_data = col

	def __str__(self):
		return str(self.row())+'-'+str(self.col())+'('+str(self.to_point())+')'

	def copy_from(self, that):
		self.row(that.row())
		self.col(that.col())

	def to_point(self):
		return self.view.text_point(self.row(), self.col())

	def from_point(self, point):
		(self.row, self.col) = self.view.rowcol(p)

	def move_rel(self, r, c):
		self.row(self.row()+r)
		self.col(self.col()+c)

	def new_line(self):
		self.row(self.row()+1)
		self.col(0)

	def row(self, new_value=None):
		if new_value is None:
			return self.row_data
		else:
			self.row_data = new_value

	def col(self, new_value=None):
		if new_value is None:
			return self.col_data
		else:
			self.col_data = new_value

class TtyBecomeTerminalCommand(sublime_plugin.TextCommand):
	READ_BY = 100
	MAX_BUFFER_SIZE = 3000
	FLUSH_TIMEOUT = 200

	BINARY = True
	BINARY_ENCODING = 'ascii'

	def __init__(self, view):
		self.process = None
		self.running = False

		self.master = None

		self.cursor_pos = CursorPos(view, 0, 0)
		self.next_cursor_pos = CursorPos(view, 0, 0)

		self.buffer = ''
		self.buffer_size = 0

		# Parsing state
		self.esc_chars = ''

		sublime_plugin.TextCommand.__init__(self, view)

	#
	# Utils
	def replace(self, start, end, new_text):
		replace_view_region(self.view, start, end, new_text)

	def add_line(self):
		n = self.view.size()
		self.replace(n, n, '\n')

	def assert_running(self):
		st_assert(self.running)

	def update_running_info(self):
		self.running = self.process.poll() is None

		return self.running

	def read(self):
		data = self.master.read(self.READ_BY)
		if not data:
			return None
		if self.BINARY:
			return data.decode(self.BINARY_ENCODING)
		return data

	#
	# Cursor control
	def update_sel(self):
		# pass
		sel = self.view.sel()
		sel.clear()
		reg = sublime.Region(self.cursor_pos.to_point(), self.cursor_pos.to_point())
		sel.add(reg)

		self.view.show_at_center(reg)

	def move_to_new_pos(self):
		self.cursor_pos.copy_from(self.next_cursor_pos)

	#
	# Input
	def send_chars(self, chars):
		self.assert_running()

		if self.BINARY:
			chars = bytearray(chars, self.BINARY_ENCODING)
		self.master.write(chars)

	def send_escaped(self, chars):
		self.send_chars('\x1B'+chars)

	def send_eof(self):
		self.send_chars('\x04')

	#
	# Buffer
	def flush(self):
		if self.buffer_size == 0:
			return

		# st_debug(self.cursor_pos, self.next_cursor_pos)
		self.replace(self.cursor_pos.to_point(), self.next_cursor_pos.to_point(), self.buffer)
		self.move_to_new_pos()

		self.buffer = ''
		self.buffer_size = 0

	def append_char(self, c):
		st_assert(len(c) == 1)

		self.buffer += c
		self.buffer_size += 1

		if self.buffer_size > self.MAX_BUFFER_SIZE:
			self.flush()

	#
	# Actual work
	def new_data(self, str):
		for c in str:
			if c == '\r':
				self.flush()

				self.cursor_pos.col(0)
				self.next_cursor_pos.col(0)
			elif c == '\n':
				self.add_line()
				self.cursor_pos.move_rel(1, 0)
				self.next_cursor_pos.move_rel(1, 0)
			elif c == '\b':
				self.flush()

				self.cursor_pos.move_rel(0, -1)
				self.next_cursor_pos.move_rel(0, -1)
			else:
				self.append_char(c)
				self.next_cursor_pos.move_rel(0, 1)

	def run_command(self, cmd):
		self.view.set_name(cmd)

		(masterfd, slavefd) = pty.openpty()
		try:
			old_flags = fcntl.fcntl(masterfd, fcntl.F_GETFL)
			fcntl.fcntl(masterfd, fcntl.F_SETFL, old_flags|os.O_NONBLOCK)

			mstr = os.fdopen(masterfd, 'r+'+('b' if self.BINARY else ''), 0)
			self.master = mstr

			new_env = os.environ
			new_env['TERM'] = 'xterm-256color'

			argv = shlex.split(cmd)
			with subprocess.Popen(argv, stdin=slavefd, stdout=slavefd, stderr=slavefd, env=new_env) as proc:
				self.process = proc

				terminals.add_view(self.view, self.process, self)

				while self.update_running_info():
					try:
						while True:
							str = self.read()
							if str is None:
								break
							self.new_data(str)
						self.flush()
						# self.update_sel()
					except:
						self.view.set_name(cmd+' <ERROR>')
						proc.kill()
						raise
		finally:
			try:
				os.close(masterfd)
				os.close(slavefd)
			except OSError:
				pass
		self.view.set_name(cmd+' <finished>')


	def run(self, _, command):
		def work():
			try:
				self.run_command(command)
			except:
				st_exception_in('tty_become_terminal')
			finally:
				st_debug('TTY thread finished:', term_death_message(self.view, self.process))

				terminals.try_del_view(self.view)

		threading.Thread(target=work, name='TTY').start()

class TtyOpenCommand(sublime_plugin.ApplicationCommand):
	def run(self):
		term = sublime.active_window().new_file()
		term.settings().set('scroll_past_end', True)
		term.set_scratch(True)

		os.chdir('/Users/maximsmol')
		term.run_command('tty_become_terminal', {'command': 'bash -li'})

class TtySendCharCodesCommand(sublime_plugin.TextCommand):
	def run(self, _, codes):
		termdata = terminals.get_data_of(self.view)
		st_assert(termdata)

		str = ''
		for c in codes:
			str += chr(c)

		termdata.cmd_instance.send_chars(str)

class TtySendCharsCommand(sublime_plugin.TextCommand):
	def run(self, _, chars):
		termdata = terminals.get_data_of(self.view)
		st_assert(termdata)

		termdata.cmd_instance.send_chars(chars)

class TtySendEscapedCommand(sublime_plugin.TextCommand):
	def run(self, _, chars):
		termdata = terminals.get_data_of(self.view)
		st_assert(termdata)

		termdata.cmd_instance.send_escaped(chars)

class TtySendSignalCommand(sublime_plugin.TextCommand):
	def run(self, _, signal_name):
		termdata = terminals.get_data_of(self.view)
		st_assert(termdata)

		try:
			proc = termdata.process
			st_assert(proc, proc.poll() is None)

			st_debug('Sent signal', signal_name)

			i = SIGNALS.index(signal_name)
			proc.send_signal(SIGNAL_CODES[i])
		except ValueError:
			st_error('No such signal: '+signal_name)
			return

SIGNALS = ['SIGHUP', 'SIGINT', 'SIGQUIT', 'SIGILL', 'SIGTRAP', 'SIGABRT', 'SIGEMT', 'SIGFPE', 'SIGKILL', 'SIGBUS', 'SIGSEGV', 'SIGSYS', 'SIGPIPE', 'SIGALRM', 'SIGTERM', 'SIGUSR1', 'SIGUSR2', 'SIGCHLD', 'SIGWINCH', 'SIGURG', 'SIGSTOP', 'SIGTSTP', 'SIGCONT', 'SIGTTIN', 'SIGTTOU', 'SIGVTALRM', 'SIGPROF', 'SIGXCPU', 'SIGXFSZ']
SIGNAL_CODES = [signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGILL, signal.SIGTRAP, signal.SIGABRT, signal.SIGEMT, signal.SIGFPE, signal.SIGKILL, signal.SIGBUS, signal.SIGSEGV, signal.SIGSYS, signal.SIGPIPE, signal.SIGALRM, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2, signal.SIGCHLD, signal.SIGWINCH, signal.SIGURG, signal.SIGSTOP, signal.SIGTSTP, signal.SIGCONT, signal.SIGTTIN, signal.SIGTTOU, signal.SIGVTALRM, signal.SIGPROF, signal.SIGXCPU, signal.SIGXFSZ]
class TtyListCommand(sublime_plugin.ApplicationCommand):
	def __init__(self):
		self.window = None

		self.view_indices = {}

		self.process = None

		sublime_plugin.ApplicationCommand.__init__(self)

	def on_signal_choice(self, i):
		if i == -1:
			return

		st_assert(self.process, self.process.poll() is None)

		self.process.send_signal(SIGNAL_CODES[i])

	def on_terminal_choice(self, i):
		if i == -1:
			return

		self.process = terminals.get_data_of(self.view_indices[i]).process
		st_assert(self.process, self.process.poll() is None)

		self.window.show_quick_panel(SIGNALS, self.on_signal_choice)

	def run(self):
		self.window = sublime.active_window()
		my_id = self.window.active_view().buffer_id()

		view_names = {}
		for w in sublime.windows():
			for v in w.views():
				view_names[v.buffer_id()] = v.name()

		i = 0
		sel = -1
		self.view_indices = {}
		list = []
		for v in terminal_views:
			if v == my_id:
				sel = i

			self.view_indices[i] = v
			list.append(view_names[v]+'('+str(v)+') - pid:'+str(terminal_views[v]['proc'].pid))

			i += 1

		self.window.show_quick_panel(list if list else ['No terminals'], self.on_terminal_choice, sublime.KEEP_OPEN_ON_FOCUS_LOST, sel if sel != -1 else 0)

class TtyEventListener(sublime_plugin.EventListener):
	def on_close(self, view):
		try:
			proc = terminals.get_data_of(view).process

			if proc is not None and proc.poll() is None:
				st_debug('on_close: Killed process running in terminal:', term_death_message(view, proc))
				proc.kill()

				terminals.del_view(view)
		except:
			st_exception_in('on_close')

	def on_query_context(self, view, key, _, __, ___):
		try:
			if key == 'tty_is_in_terminal' and terminals.get_data_of(view):
				return True

			return None
		except:
			st_exception_in('on_query_context')

def plugin_unloaded():
	st_debug('Killing all processes running in terminals')
	for id in terminals.data:
		proc = terminals.data[id].process
		if proc is not None and proc.poll() is None:
			proc.kill()
