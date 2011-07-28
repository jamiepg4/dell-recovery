#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-bootstrap» - Ubiquity plugin for Dell Factory Process
#
# Copyright (C) 2010-2011, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>
#
# This is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this application; if not, write to the Free Software Foundation, Inc., 51
# Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
################################################################################

from ubiquity.plugin import InstallPlugin, Plugin, PluginUI
from ubiquity import misc
from threading import Thread
from Dell.recovery_threading import ProgressBySize
import debconf
import Dell.recovery_common as magic
from Dell.recovery_xml import BTOxml
import subprocess
import os
import re
import shutil
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import syslog
import glob
import zipfile
import tarfile
import hashlib
from apt.cache import Cache

NAME = 'dell-bootstrap'
BEFORE = 'language'
WEIGHT = 12
OEM = False

STANDARD_EFI_PARTITION =     '1'
STANDARD_UP_PARTITION  =     '1'
STANDARD_RP_PARTITION  =     '2'
CDROM_MOUNT = '/cdrom'
ISO_MOUNT = '/isodevice'

TYPE_NTFS = '07'
TYPE_NTFS_RE = '27'
TYPE_VFAT = '0b'
TYPE_VFAT_LBA = '0c'

#Continually Reused ubiquity templates
RECOVERY_TYPE_QUESTION =  'dell-recovery/recovery_type'
DUAL_BOOT_QUESTION = 'dell-recovery/dual_boot'
DUAL_BOOT_LAYOUT_QUESTION = 'dell-recovery/dual_boot_layout'
ACTIVE_PARTITION_QUESTION = 'dell-recovery/active_partition'
FAIL_PARTITION_QUESTION = 'dell-recovery/fail_partition'
DISK_LAYOUT_QUESTION = 'dell-recovery/disk_layout'
SWAP_QUESTION = 'dell-recovery/swap'
RP_FILESYSTEM_QUESTION = 'dell-recovery/recovery_partition_filesystem'
DRIVER_INSTALL_QUESTION = 'dell-recovery/disable-driver-install'
USER_INTERFACE_QUESTION = 'dell-oobe/user-interface'
OIE_QUESTION = 'dell-recovery/oie_mode'

#######################
# Noninteractive Page #
#######################
class PageNoninteractive(PluginUI):
    """Non-Interactive frontend for the dell-bootstrap ubiquity plugin"""
    def __init__(self, controller, *args, **kwargs):
        self.controller = controller
        PluginUI.__init__(self, controller, *args, **kwargs)
    
    def get_type(self):
        '''For the noninteractive frontend, get_type always returns an empty str
            This is because the noninteractive frontend always runs in "factory"
            mode, which expects such a str""'''
        return ""

    def set_type(self, value):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def show_dialog(self, which, data = None):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def get_selected_device(self):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def populate_devices(self, devices):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def set_advanced(self, item, value):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def get_advanced(self, item):
        """Empty skeleton function for the non-interactive UI"""
        return ''

############
# GTK Page #
############
class PageGtk(PluginUI):
    """GTK frontend for the dell-bootstrap ubiquity plugin"""
    #OK, so we're not "really" a language page
    #We are just cheating a little bit to make sure our widgets are translated
    plugin_is_language = True

    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None

        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ

        self.efi = False
        self.genuine = magic.check_vendor()

        if not oem:
            import gtk
            builder = gtk.Builder()
            builder.add_from_file('/usr/share/ubiquity/gtk/stepDellBootstrap.ui')
            builder.connect_signals(self)
            self.controller = controller
            self.controller.add_builder(builder)
            self.plugin_widgets = builder.get_object('stepDellBootstrap')
            self.automated_recovery = builder.get_object('automated_recovery')
            self.automated_recovery_box = builder.get_object('automated_recovery_box')
            self.automated_combobox = builder.get_object('hard_drive_combobox')
            self.interactive_recovery = builder.get_object('interactive_recovery')
            self.interactive_recovery_box = builder.get_object('interactive_recovery_box')
            self.hdd_recovery = builder.get_object('hdd_recovery')
            self.hdd_recovery_box = builder.get_object('hdd_recovery_box')
            self.hidden_radio = builder.get_object('hidden_radio')
            self.reboot_dialog = builder.get_object('reboot_dialog')
            self.reboot_dialog.set_title('Dell Recovery')
            self.dual_dialog = builder.get_object('dual_dialog')
            self.dual_dialog.set_title('Dell Recovery')
            self.info_box = builder.get_object('info_box')
            self.info_spinner = gtk.Spinner()
            builder.get_object('info_spinner_box').add(self.info_spinner)
            self.err_dialog = builder.get_object('err_dialog')

            #advanced page widgets
            icon = builder.get_object('dell_image')
            icon.set_tooltip_markup("Dell Recovery Advanced Options")
            self.advanced_page = builder.get_object('advanced_window')
            self.advanced_table = builder.get_object('advanced_table')
            self.version_detail = builder.get_object('version_detail')
            self.mount_detail = builder.get_object('mountpoint_detail')
            self.memory_detail = builder.get_object('memory_detail')
            self.proprietary_combobox = builder.get_object('disable_proprietary_driver_combobox')
            self.dual_combobox = builder.get_object('dual_combobox')
            self.dual_layout_combobox = builder.get_object('dual_layout_combobox')
            self.active_partition_combobox = builder.get_object('active_partition_combobox')
            self.rp_filesystem_combobox = builder.get_object('recovery_partition_filesystem_checkbox')
            self.disk_layout_combobox = builder.get_object('disk_layout_combobox')
            self.swap_combobox = builder.get_object('swap_behavior_combobox')
            self.ui_combobox = builder.get_object('default_ui_combobox')
            self.oie_combobox = builder.get_object('oie_combobox')

            #populate dynamic comboboxes
            self._populate_dynamic_comoboxes()

            if not (self.genuine and 'UBIQUITY_AUTOMATIC' in os.environ):
                builder.get_object('error_box').show()
            PluginUI.__init__(self, controller, *args, **kwargs)

    def plugin_get_current_page(self):
        """Called when ubiquity tries to realize this page.
           * Disable the progress bar
           * Check whether we are on genuine hardware
        """
        #are we real?
        if not (self.genuine and 'UBIQUITY_AUTOMATIC' in os.environ):
            self.advanced_table.set_sensitive(False)
            self.interactive_recovery_box.hide()
            self.automated_recovery_box.hide()
            self.automated_recovery.set_sensitive(False)
            self.interactive_recovery.set_sensitive(False)
            self.controller.allow_go_forward(False)
        self.toggle_progress()

        return self.plugin_widgets

    def toggle_progress(self):
        """Toggles the progress bar for RP build"""
        if 'UBIQUITY_AUTOMATIC' in os.environ and \
                            hasattr(self.controller, 'toggle_progress_section'):
            self.controller.toggle_progress_section()

    def get_type(self):
        """Returns the type of recovery to do from GUI"""
        if self.automated_recovery.get_active():
            return "automatic"
        elif self.interactive_recovery.get_active():
            return "interactive"
        else:
            return ""

    def get_selected_device(self):
        """Returns the selected device from the GUI"""
        device = size = ''
        model = self.automated_combobox.get_model()
        iterator = self.automated_combobox.get_active_iter()
        if iterator is not None:
            device = model.get_value(iterator, 0)
            size = model.get_value(iterator, 1)
        return (device, size)

    def set_type(self, value):
        """Sets the type of recovery to do in GUI"""
        if not self.genuine:
            return
        self.hidden_radio.set_active(True)

        if value == "automatic":
            self.automated_recovery.set_active(True)
        elif value == "interactive":
            self.interactive_recovery.set_active(True)
        elif value == "factory":
            self.plugin_widgets.hide()
        else:
            self.controller.allow_go_forward(False)
            if value == "hdd":
                self.advanced_table.set_sensitive(False)
                self.hdd_recovery_box.show()
                self.interactive_recovery_box.hide()
                self.automated_recovery_box.hide()
                self.interactive_recovery.set_sensitive(False)
                self.automated_recovery.set_sensitive(False)

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)
        self.automated_combobox.set_sensitive(self.automated_recovery.get_active())

    def show_dialog(self, which, data = None):
        """Shows a dialog"""
        if which == "info":
            self.controller._wizard.quit.set_label(
                         self.controller.get_string('ubiquity/imported/cancel'))
            self.controller.allow_go_forward(False)
            self.automated_recovery_box.hide()
            self.interactive_recovery_box.hide()
            self.info_box.show_all()
            self.info_spinner.start()
            self.toggle_progress()
        elif which == "forward":
            self.automated_recovery_box.hide()
            self.interactive_recovery_box.hide()
        else:
            self.info_spinner.stop()
            if which == "exception":
                self.err_dialog.format_secondary_text(str(data))
                self.err_dialog.run()
                self.err_dialog.hide()
                return

            self.controller.toggle_top_level()
            if which == "reboot":
                self.reboot_dialog.run()

            elif which == DUAL_BOOT_QUESTION:
                self.dual_dialog.run()

    def populate_devices(self, devices):
        """Feeds a selection of devices into the GUI
           devices should be an array of 3 column arrays
        """
        #populate the devices
        liststore = self.automated_combobox.get_model()
        for device in devices:
            liststore.append(device)

        #default to the first item active (it should be sorted anyway)
        self.automated_combobox.set_active(0)

    ##                      ##
    ## Advanced GUI options ##
    ##                      ##
    def toggle_advanced(self, widget, data = None):
        """Shows the advanced page"""
        self.plugin_widgets.set_sensitive(False)
        self.advanced_page.run()
        self.advanced_page.hide()
        self.plugin_widgets.set_sensitive(True)

    def _populate_dynamic_comoboxes(self):
        """Fills up comboboxes with dynamic items based on the squashfs"""
        liststore = self.ui_combobox.get_model()
        uies = magic.find_supported_ui()
        for item in uies:
            liststore.append([item,uies[item]])

    def _map_combobox(self, item):
        """Maps a combobox to a question"""
        combobox = None
        if item == USER_INTERFACE_QUESTION:
            combobox = self.ui_combobox
        elif item == OIE_QUESTION:
            combobox = self.oie_combobox
        elif item == DRIVER_INSTALL_QUESTION:
            combobox = self.proprietary_combobox
        elif item == ACTIVE_PARTITION_QUESTION:
            combobox = self.active_partition_combobox
        elif item == RP_FILESYSTEM_QUESTION:
            combobox = self.rp_filesystem_combobox
        elif item == DISK_LAYOUT_QUESTION:
            combobox = self.disk_layout_combobox
        elif item == SWAP_QUESTION:
            combobox = self.swap_combobox
        elif item == DUAL_BOOT_QUESTION:
            combobox = self.dual_combobox
        elif item == DUAL_BOOT_LAYOUT_QUESTION:
            combobox = self.dual_layout_combobox
        return combobox

    def set_advanced(self, item, value):
        """Populates the options that should be on the advanced page"""

        if item == 'efi' and value:
            self.efi = True
            self.disk_layout_combobox.set_sensitive(False)
            self.active_partition_combobox.set_sensitive(False)
            self.dual_combobox.set_sensitive(False)
        elif item == "mem" and value:
            self.memory_detail.set_markup("Total Memory: %f GB" % value)
        elif item == "version":
            self.version_detail.set_markup("Version: %s" % value)
        elif item == "mount":
            self.mount_detail.set_markup("Mounted From: %s" % value)
        else:
            if type(value) is bool:
                if value:
                    value = 'true'
                else:
                    value = 'false'
            combobox = self._map_combobox(item)
            if combobox:
                iterator = find_item_iterator(combobox, value)
                if iterator is not None:
                    combobox.set_active_iter(iterator)
                else:
                    syslog.syslog("DEBUG: setting %s to %s failed" % \
                                                                  (item, value))
                    combobox.set_active(0)

            #dual boot mode. ui changes for this
            if item == DUAL_BOOT_QUESTION and self.genuine:
                value = misc.create_bool(value)
                self.dual_layout_combobox.set_sensitive(value)
                if value:
                    self.interactive_recovery_box.hide()
                else:
                    self.interactive_recovery_box.show()
                self.interactive_recovery.set_sensitive(not value)

    def get_advanced(self, item):
        """Returns the value in an advanced key"""
        combobox = self._map_combobox(item)
        if combobox:
            return combobox.get_active_text()
        else:
            return ""
 
    def advanced_callback(self, widget, data = None):
        """Callback when an advanced widget is toggled"""
        if widget == self.proprietary_combobox:
            #nothing changes if we change proprietary drivers currently
            pass
        elif widget == self.active_partition_combobox:
            #nothing changes if we change active partition currently
            pass
        elif widget == self.rp_filesystem_combobox:
            #nothing changes if we change RP filesystem currently
            pass
        elif widget == self.swap_combobox:
            #nothing change if we change swap currently
            pass
        else:
            model = widget.get_model()
            iterator = widget.get_active_iter()
            if iterator is not None:
                answer = model.get_value(iterator, 0)
                
            if widget == self.disk_layout_combobox:
                if answer == "gpt":
                    find_n_set_iterator(self.active_partition_combobox, \
                                                         STANDARD_EFI_PARTITION)
                    self.active_partition_combobox.set_sensitive(False)
                else:
                    self.active_partition_combobox.set_sensitive(True)
            elif widget == self.dual_combobox:
                answer = misc.create_bool(answer)
                if not self.efi:
                    #set the type back to msdos
                    find_n_set_iterator(self.disk_layout_combobox, "msdos")
                    self.disk_layout_combobox.set_sensitive(not answer)
                #hide in the UI - this is a little special because it hides
                #some basic settings too
                self.set_advanced(DUAL_BOOT_QUESTION, answer)

################
# Debconf Page #
################
class Page(Plugin):
    """Debconf driven page for the dell-bootstrap ubiquity plugin"""
    def __init__(self, frontend, db=None, ui=None):
        self.device = None
        self.device_size = 0
        self.efi = False
        self.preseed_config = ''
        self.rp_builder = None
        self.os_part = None
        self.disk_size = None
        self.rp_filesystem = None
        self.fail_partition = None
        self.disk_layout = None
        self.swap_part = None
        self.swap = None
        self.dual = None
        self.uuid = None
        self.rp_part = None
        self.grub_part = None
        Plugin.__init__(self, frontend, db, ui)

    def log(self, error):
        """Outputs a debugging string to /var/log/installer/debug"""
        self.debug("%s: %s" % (NAME, error))

    def install_grub(self):
        """Installs grub on the recovery partition"""

        # In a lot of scenarios it will already be there.
        # Don't install if:
        # * We're dual boot
        # * We're GPT (or EFI)
        # * We're on an NTFS filesystem
        # * Factory grub exists (ntldr)
        # * Grubenv exists (grubenv)
        if self.dual or self.disk_layout == 'gpt' or \
                self.rp_filesystem == TYPE_NTFS or \
                self.rp_filesystem == TYPE_NTFS_RE or \
                os.path.exists(os.path.join(CDROM_MOUNT, 'ntldr')) or \
                os.path.exists(os.path.join(CDROM_MOUNT, 'boot', 'grub',
                                                        'i386-pc', 'grubenv')):
            return

        #test for bug 700910.  if around, then don't try to install grub.
        try:
            test700910 = magic.fetch_output(['grub-probe', '--target=device', self.device + STANDARD_RP_PARTITION]).strip()
        except Exception, e:
            self.log("Exception: %s." % str(e))
            test700910 = 'aufs'
        if test700910 == 'aufs':
            self.log("Bug 700910 detected.  Aborting GRUB installation.")
            return

        self.log("Installing GRUB to %s" % self.device + STANDARD_RP_PARTITION)

        #Mount R/W
        if os.path.exists(ISO_MOUNT):
            target = ISO_MOUNT
        else:
            target = CDROM_MOUNT
        cd_mount   = misc.execute_root('mount', '-o', 'remount,rw', target)
        if cd_mount is False:
            raise RuntimeError, ("CD Mount failed")

        #Check for a grub.cfg to start - make as necessary
        files = {'recovery_partition.cfg': 'grub.cfg',
                 'common.cfg' : 'common.cfg'}
        for item in files:
            if not os.path.exists(os.path.join(target, 'boot', 'grub', files[item])):
                with misc.raised_privileges():
                    magic.process_conf_file('/usr/share/dell/grub/' + item,   \
                              os.path.join(target, 'boot', 'grub', files[item]), \
                              self.uuid, STANDARD_RP_PARTITION)

        #Do the actual grub installation
        grub_inst  = misc.execute_root('grub-install', '--force', \
                                            '--root-directory=' + target, \
                                            self.device + STANDARD_RP_PARTITION)
        if grub_inst is False:
            raise RuntimeError, ("Grub install failed")
        uncd_mount = misc.execute_root('mount', '-o', 'remount,ro', target)
        if uncd_mount is False:
            syslog.syslog("Uncd mount failed.  This may be normal depending on the filesystem.")

    def disable_swap(self):
        """Disables any swap partitions in use"""
        bus = dbus.SystemBus()

        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        udisk_int = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = udisk_int.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            #Find mounted swap
            if dev.Get('org.freedesktop.UDisks.Device', 'IdType') == 'swap':
                device = dev.Get('org.freedesktop.Udisks.Device', 'DeviceFile')
                misc.execute_root('swapoff', device)
                if misc is False:
                    raise RuntimeError, ("Error removing swap for device %s" % \
                                                                         device)

    def test_oie(self):
        """Prepares the installation for running in OIE mode"""
        if self.oie:
            #rewrite the bootsector of the UP (it might not have been created
            #if the ODM is using a WIM)
            with misc.raised_privileges():
                magic.write_up_bootsector(self.device, STANDARD_UP_PARTITION)
            #turn off machine when OIE process is done
            self.preseed('ubiquity/poweroff', 'true')
            self.preseed('ubiquity/reboot',   'false')
            #so that if we fail we can red screen
            with open('/tmp/oie', 'w') as wfd:
                pass

    def sleep_network(self):
        """Requests the network be disabled for the duration of install to
           prevent conflicts"""
        bus = dbus.SystemBus()
        backend_iface = dbus.Interface(bus.get_object(magic.DBUS_BUS_NAME, '/RecoveryMedia'), magic.DBUS_INTERFACE_NAME)
        backend_iface.force_network(False)
        backend_iface.request_exit() 

    def clean_recipe(self):
        """Cleans up the recipe to remove swap if we have a small drive"""

        #don't mess with dual boot recipes
        if self.dual:
            return

        #If we are in dynamic (dell-recovery/swap=dynamic) and small drive 
        #   or we explicitly disabled (dell-recovery/swap=false)
        if not self.swap or (self.swap == "dynamic" and \
                                       (self.mem >= 4 or self.disk_size <= 64)):
            self.log("Performing swap recipe fixup (%s, hdd: %i, mem: %f)" % \
                                        (self.swap, self.disk_size, self.mem))
            try:
                recipe = self.db.get('partman-auto/expert_recipe')
                self.db.set('partman-auto/expert_recipe',
                                                     recipe.split('.')[0] + '.')
            except debconf.DebconfError, err:
                self.log(str(err))

    def remove_extra_partitions(self):
        """Removes partitions we are installing on for the process to start"""
        if self.disk_layout == 'msdos':
            #First set the new partition active
            active = misc.execute_root('sfdisk', '-A%s' % self.fail_partition, \
                                                                    self.device)
            if active is False:
                self.log("Failed to set partition %s active on %s" % \
                                             (self.fail_partition, self.device))
        #check for small disks.
        #on small disks or big mem, don't look for extended or delete swap.
        if not self.swap or (self.swap == "dynamic" and \
                                       (self.mem >= 4 or self.disk_size <= 64)):
            self.swap_part = ''
            total_partitions = 0
        else:
            #check for extended partitions
            with misc.raised_privileges():
                total_partitions = len(magic.fetch_output(['partx', self.device]).split('\n'))-1
        #remove extras
        for number in (self.os_part, self.swap_part):
            if number.isdigit():
                remove = misc.execute_root('parted', '-s', self.device, 'rm', number)
                if remove is False:
                    self.log("Error removing partition number: %s on %s (this may be normal)'" % (number, self.device))
                refresh = misc.execute_root('partx', '-d', '--nr', number, self.device)
                if refresh is False:
                    self.log("Error updating partition %s for kernel device %s (this may be normal)'" % (number, self.device))
        #if there were extended, cleanup
        if total_partitions > 4:
            refresh = misc.execute_root('partx', '-d', '--nr', '5-' + str(total_partitions), self.device)
            if refresh is False:
                self.log("Error removing extended partitions 5-%s for kernel device %s (this may be normal)'" % (total_partitions, self.device))

    def explode_sdr(self):
        '''Explodes all content explicitly defined in an SDR
           If no SDR was found, don't change drive at all
        '''
        sdr_file = glob.glob(CDROM_MOUNT + "/*SDR")
        if not sdr_file:
            sdr_file = glob.glob(ISO_MOUNT + "/*SDR")
        if not sdr_file:
            return

        #RP Needs to be writable no matter what
        if not os.path.exists(ISO_MOUNT):
            cd_mount = misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)
            if cd_mount is False:
                raise RuntimeError, ("Error remounting RP to explode SDR.")

        #Parse SDR
        srv_list = []
        dest = 'US'
        with open(sdr_file[0], 'r') as rfd:
            sdr_lines = rfd.readlines()
        for line in sdr_lines:
            if line.startswith('SI'):
                columns = line.split()
                if len(columns) > 2:
                    #always assume lower case (in case case sensitive FS)
                    srv_list.append(columns[2].lower())
            if line.startswith('HW'):
                columns = line.split()
                if len(columns) > 2 and columns[1] == 'destination':
                    dest = columns[2]

        #Explode SRVs that match SDR
        for srv in srv_list:
            fname = os.path.join(os.path.join(CDROM_MOUNT, 'srv', '%s' % srv))
            if os.path.exists('%s.tgz' % fname):
                archive = tarfile.open('%s.tgz' % fname)
            elif os.path.exists('%s.zip' % fname):
                archive = zipfile.ZipFile('%s.zip' % fname)
            else:
                self.log("Skipping SRV %s. No file on filesystem." % srv)
                continue
            with misc.raised_privileges():
                self.log("Extracting SRV %s onto filesystem" % srv)
                archive.extractall(path=CDROM_MOUNT)
            archive.close()

        #if the destination is somewhere special, change the language
        if dest == 'CN':
            self.preseed('debian-installer/locale', 'zh_CN')
            self.ui.controller.translate('zh_CN')

    def explode_utility_partition(self):
        '''Explodes all content onto the utility partition
        '''

        #Check if we have FIST on the system.  FIST indicates this is running
        #through factory process (of some sort) and the UP will be written
        #out outside of our control
        cache = Cache()
        for key in cache.keys():
            if key == 'fist' and cache[key].is_installed:
                self.log("FIST was found, not building a UP.")
                return
        del cache

        #For now on GPT we don't include an UP since we can't boot
        # 16 bit code as necessary for the UP to be working
        if self.disk_layout == 'gpt':
            self.log("A GPT layout was found, not building a UP.")
            return

        mount = False
        path = ''
        if os.path.exists('/usr/share/dell/up/drmk.zip'):
            path = '/usr/share/dell/up/drmk.zip'
        elif os.path.exists(os.path.join(CDROM_MOUNT, 'misc', 'drmk.zip')):
            path = os.path.join(CDROM_MOUNT, 'misc', 'drmk.zip')
        #If we have DRMK available, explode that first
        if path:
            self.log("Extracting DRMK onto utility partition %s" % self.device + STANDARD_UP_PARTITION)
            mount = misc.execute_root('mount', self.device + STANDARD_UP_PARTITION, '/boot')
            if mount is False:
                raise RuntimeError, ("Error mounting utility partition pre-explosion.")
            archive = zipfile.ZipFile(path)
            with misc.raised_privileges():
                try:
                    archive.extractall(path='/boot')
                except IOError, msg:
                    #Partition is corrupted, abort doing anything else here but don't
                    #fail the install
                    #TODO ML (1/10/11) - instead rebuild the UP if possible.
                    self.log("Ignoring corrupted utility partition(%s)." % msg)
                    return
            archive.close()

        #Now check for additional UP content to explode
        for fname in magic.UP_FILENAMES:
            if os.path.exists(os.path.join(CDROM_MOUNT, fname)):
                #Restore full UP backup (dd)
                if '.bin' in fname or '.gz' in fname:
                    self.log("Exploding utility partition from %s" % fname)
                    with misc.raised_privileges():
                        with open(self.device + STANDARD_UP_PARTITION, 'w') as partition:
                            p1 = subprocess.Popen(['gzip', '-dc', os.path.join(CDROM_MOUNT, fname)], stdout=subprocess.PIPE)
                            partition.write(p1.communicate()[0])
                #Restore UP (zip/tgz)
                elif '.zip' in fname or '.tgz' in fname:
                    self.log("Extracting utility partition from %s" % fname)
                    if not mount:
                        mount = misc.execute_root('mount', self.device + STANDARD_UP_PARTITION, '/boot')
                        if mount is False:
                            raise RuntimeError, ("Error mounting utility partition pre-explosion.")
                    if '.zip' in fname:
                        archive = zipfile.ZipFile(os.path.join(CDROM_MOUNT, fname))
                    elif '.tgz' in file:
                        archive = tarfile.open(os.path.join(CDROM_MOUNT, fname))
                    with misc.raised_privileges():
                        archive.extractall(path='/boot')
                    archive.close()
        #If we didn't include an autoexec.bat (as is the case from normal DellDiags releases)
        #Then make the files we need to be automatically bootable
        if not os.path.exists('/boot/autoexec.bat') and os.path.exists('/boot/autoexec.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/autoexec.up', '/boot/autoexec.bat')
        if not os.path.exists('/boot/config.sys') and os.path.exists('/boot/config.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/config.up', '/boot/config.sys')
        if mount:
            umount = misc.execute_root('umount', '/boot')
            if umount is False:
                raise RuntimeError, ("Error unmounting utility partition post-explosion.")


    def boot_rp(self):
        """reboots the system"""
        with open ('/proc/cmdline', 'r') as rfd:
            noprompt = 'noprompt' in rfd.readline()

        #only cache casper if it's not going to ask the user to eject the media
        if noprompt:
            #Set up a listen for udisks to let us know a usb device has left
            subprocess.call(['/etc/init.d/casper', 'stop'])

            bus = dbus.SystemBus()
            bus.add_signal_receiver(reboot_machine, 'DeviceRemoved', 'org.freedesktop.UDisks')

        if self.dual:
            dialog = DUAL_BOOT_QUESTION
        else:
            dialog = "reboot"

        self.ui.show_dialog(dialog)
        
        reboot_machine(None)

    def unset_drive_preseeds(self):
        """Unsets any preseeds that are related to setting a drive"""
        for key in [ 'partman-auto/init_automatically_partition',
                     'partman-auto/disk',
                     'partman-auto/expert_recipe',
                     'partman-basicfilesystems/no_swap',
                     'grub-installer/only_debian',
                     'grub-installer/with_other_os',
                     'grub-installer/bootdev',
                     'grub-installer/make_active',
                     'oem-config/early_command',
                     'oem-config/late_command',
                     'dell-recovery/active_partition',
                     'dell-recovery/fail_partition',
                     'ubiquity/poweroff',
                     'ubiquity/reboot' ]:
            self.db.fset(key, 'seen', 'false')
            self.db.set(key, '')
        self.db.set('ubiquity/partman-skip-unmount', 'false')
        self.db.set('partman/filter_mounted', 'true')

    def fixup_recovery_devices(self):
        """Discovers the first hard disk to install to"""
        bus = dbus.SystemBus()
        disks = []

        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        udi = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = udi.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            #Skip USB, Removable Disks, Partitions, External, Loopback, Readonly
            if dev.Get('org.freedesktop.UDisks.Device', 'DriveConnectionInterface') == 'usb' or \
               dev.Get('org.freedesktop.UDisks.Device', 'DeviceIsRemovable') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device', 'DeviceIsPartition') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device', 'DeviceIsSystemInternal') == 0 or \
               dev.Get('org.freedesktop.UDisks.Device', 'DeviceIsLinuxLoop') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device', 'DeviceIsReadOnly') == 1 :
                continue

            #if we made it this far, add it
            devicefile = dev.Get('org.freedesktop.Udisks.Device',   'DeviceFile')
            devicemodel = dev.Get('org.freedesktop.Udisks.Device',  'DriveModel')
            devicevendor = dev.Get('org.freedesktop.Udisks.Device', 'DriveVendor')
            devicesize = dev.Get('org.freedesktop.Udisks.Device',   'DeviceSize')
            devicesize_gb = "%i" % (devicesize / 1000000000)
            disks.append([devicefile, devicesize, "%s GB %s %s (%s)" % (devicesize_gb, devicevendor, devicemodel, devicefile)])

        #If multiple candidates were found, record in the logs
        if len(disks) == 0:
            raise RuntimeError, ("Unable to find and candidate hard disks to install to.")
        if len(disks) > 1:
            disks.sort()
            self.log("Multiple disk candidates were found: %s" % disks)

        #Always choose the first candidate to start
        self.device = disks[0][0]
        self.log("Initially selected candidate disk: %s" % self.device)

        #populate UI
        self.ui.populate_devices(disks)

    def fixup_factory_devices(self, rec_part):
        """Find the factory recovery partition, and re-adjust preseeds to use that data"""
        #Ignore any EDD settings - we want to just plop on the same drive with
        #the right FS label (which will be valid right now)
        #Don't you dare put a USB stick in the system with that label right now!

        self.device = rec_part["slave"]

        if os.path.exists(ISO_MOUNT):
            location = ISO_MOUNT
        else:
            location = CDROM_MOUNT
        early = '/usr/share/dell/scripts/oem_config.sh early %s %s' % (rec_part['device'], location)
        self.db.set('oem-config/early_command', early)
        self.db.set('partman-auto/disk', self.device)

        if self.disk_layout == 'msdos':
            self.db.set('grub-installer/bootdev', self.device + self.os_part)
        elif self.disk_layout == 'gpt':
            self.db.set('grub-installer/bootdev', self.device)

        if rec_part["fs"] == "ntfs":
            self.rp_filesystem = TYPE_NTFS_RE
        elif rec_part["fs"] == "vfat":
            self.rp_filesystem = TYPE_VFAT_LBA
        else:
            raise RuntimeError, ("Unknown filesystem on recovery partition: %s" % rec_part["fs"])

        if self.dual_layout == 'logical':
            expert_question = 'partman-auto/expert_recipe'
            self.db.set(expert_question,
                    self.db.get(expert_question).replace('primary', 'logical'))
            self.db.set('ubiquity/install_bootloader', 'false')

        self.disk_size = rec_part["size_gb"]
        self.uuid = rec_part["uuid"]

        self.log("Detected device we are operating on is %s" % self.device)
        self.log("Detected a %s filesystem on the %s recovery partition" % (rec_part["fs"], rec_part["label"]))

    def prepare(self, unfiltered=False):
        """Prepare the Debconf portion of the plugin and gather all data"""
        #version
        with misc.raised_privileges():
            version = magic.check_version()
        self.log("version %s" % version)
        
        #mountpoint
        mount = ''
        mount = find_boot_device()
        self.log("mounted from %s" % mount)

        #recovery type
        rec_type = None
        try:
            rec_type = self.db.get(RECOVERY_TYPE_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            rec_type = 'dynamic'
            self.db.register('debian-installer/dummy', RECOVERY_TYPE_QUESTION)
            self.db.set(RECOVERY_TYPE_QUESTION, rec_type)

        #If we were preseeded to dynamic, look for an RP
        rec_part = magic.find_factory_rp_stats()
        if rec_type == 'dynamic':
            if rec_part and rec_part["slave"] in mount:
                self.log("Detected RP at %s, setting to factory boot" % mount)
                rec_type = 'factory'
            if not rec_part:
                self.log("No (matching) RP found.  Assuming media based boot")
                rec_type = 'dvd'

        #Media boots should be interrupted at first screen in --automatic mode
        if rec_type == 'factory':
            self.db.fset(RECOVERY_TYPE_QUESTION, 'seen', 'true')
        else:
            self.db.set(RECOVERY_TYPE_QUESTION, '')
            self.db.fset(RECOVERY_TYPE_QUESTION, 'seen', 'false')

        #In case we preseeded the partitions we need installed to
        try:
            self.os_part = self.db.get('dell-recovery/os_partition')
        except debconf.DebconfError, err:
            self.log(str(err))
            self.os_part = '3'

        try:
            self.swap_part = self.db.get('dell-recovery/swap_partition')
        except debconf.DebconfError, err:
            self.log(str(err))
            self.swap_part = '4'

        #Support cases where the recovery partition isn't a linux partition
        try:
            self.rp_filesystem = self.db.get(RP_FILESYSTEM_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            self.rp_filesystem = TYPE_VFAT_LBA

        #Check if we are set in dual-boot mode
        try:
            self.dual = misc.create_bool(self.db.get(DUAL_BOOT_QUESTION))
        except debconf.DebconfError, err:
            self.log(str(err))
            self.dual = False

        try:
            self.dual_layout = self.db.get(DUAL_BOOT_LAYOUT_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            self.dual_layout = 'primary'

        #If we are successful for an MBR install, this is where we boot to
        try:
            pass_partition = self.db.get(ACTIVE_PARTITION_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            pass_partition = self.os_part
            self.preseed(ACTIVE_PARTITION_QUESTION, pass_partition)

        #In case an MBR install fails, this is where we boot to
        try:
            self.fail_partition = self.db.get(FAIL_PARTITION_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            self.fail_partition = STANDARD_RP_PARTITION
            self.preseed(FAIL_PARTITION_QUESTION, self.fail_partition)

        #The requested disk layout type
        #This is generally for debug purposes, but will be overridden if we
        #determine that we are actually going to be doing an EFI install
        try:
            self.disk_layout = self.db.get(DISK_LAYOUT_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            self.disk_layout = 'msdos'
            self.preseed(DISK_LAYOUT_QUESTION, self.disk_layout)

        #Behavior of the swap partition
        try:
            self.swap = self.db.get(SWAP_QUESTION)
            if self.swap != "dynamic":
                self.swap = misc.create_bool(self.swap)
        except debconf.DebconfError, err:
            self.log(str(err))
            self.swap = 'dynamic'

        #Proprietary driver installation preventions
        try:
            proprietary = self.db.get(DRIVER_INSTALL_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            proprietary = ''

        #default UI
        try:
            user_interface = self.db.get(USER_INTERFACE_QUESTION)
        except debconf.DebconfError, err:
            self.log(str(err))
            user_interface = 'dynamic'
            self.preseed(USER_INTERFACE_QUESTION, user_interface)

        #test for OIE.  OIE images turn off after install
        try:
            self.oie = misc.create_bool(self.db.get(OIE_QUESTION))
        except debconf.DebconfError, err:
            self.log(str(err))
            self.oie = False

        #If we detect that we are booted into uEFI mode, then we only want
        #to do a GPT install.  Actually a MBR install would work in most
        #cases, but we can't make assumptions about 16-bit anymore (and
        #preparing a UP because of it)
        if os.path.isdir('/proc/efi') or os.path.isdir('/sys/firmware/efi'):
            self.efi = True
            self.disk_layout = 'gpt'

        #Default in EFI case, but also possible in MBR case
        if self.disk_layout == 'gpt':
            #Force EFI partition or bios_grub partition active
            self.preseed(ACTIVE_PARTITION_QUESTION, STANDARD_EFI_PARTITION)

        #Amount of memory in the system
        self.mem = 0
        if os.path.exists('/sys/firmware/memmap'):
            for root, dirs, files in os.walk('/sys/firmware/memmap', topdown=False):
                if os.path.exists(os.path.join(root, 'type')):
                    with open(os.path.join(root, 'type')) as rfd:
                        type = rfd.readline().strip('\n')
                    if type != "System RAM":
                        continue
                    with open(os.path.join(root, 'start')) as rfd:
                        start = int(rfd.readline().strip('\n'),0)
                    with open(os.path.join(root, 'end')) as rfd:
                        end = int(rfd.readline().strip('\n'),0)
                    self.mem += (end - start + 1)
            self.mem = float(self.mem/1024)
        if self.mem == 0:
            with open('/proc/meminfo','r') as rfd:
                for line in rfd.readlines():
                    if line.startswith('MemTotal'):
                        self.mem = float(line.split()[1].strip())
                        break
        self.mem = round(self.mem/1048575) #in GB

        #Fill in UI data
        twiddle = {"mount": mount,
                   "version": version,
                   DUAL_BOOT_LAYOUT_QUESTION: self.dual_layout,
                   DUAL_BOOT_QUESTION: self.dual,
                   ACTIVE_PARTITION_QUESTION: pass_partition,
                   DISK_LAYOUT_QUESTION: self.disk_layout,
                   SWAP_QUESTION: self.swap,
                   DRIVER_INSTALL_QUESTION: proprietary,
                   USER_INTERFACE_QUESTION: user_interface,
                   RP_FILESYSTEM_QUESTION: self.rp_filesystem,
                   OIE_QUESTION: self.oie,
                   "mem": self.mem,
                   "efi": self.efi}
        for twaddle in twiddle:
            self.ui.set_advanced(twaddle, twiddle[twaddle])
        self.ui.set_type(rec_type)

        #set the language in the UI
        try:
            language = self.db.get('debian-installer/language')
        except debconf.DebconfError:
            language = ''
        if not language:
            with open('/proc/cmdline', 'r') as rfd:
                for item in rfd.readline().split():
                    if 'locale=' in item:
                        items = item.split('=')
                        if len(items) > 1:
                            language = items[1]
                            break
        if language:
            self.preseed('debian-installer/locale', language)
            self.ui.controller.translate(language)

        #Clarify which device we're operating on initially in the UI
        try:
            if rec_type != 'factory' and rec_type != 'hdd':
                self.fixup_recovery_devices()
            else:
                self.fixup_factory_devices(rec_part)
        except Exception, err:
            self.handle_exception(err)
            self.cancel_handler()

        return (['/usr/share/ubiquity/dell-bootstrap'], [RECOVERY_TYPE_QUESTION])

    def ok_handler(self):
        """Copy answers from debconf questions"""
        #basic questions
        rec_type = self.ui.get_type()
        self.log("recovery type set to %s" % rec_type)
        self.preseed(RECOVERY_TYPE_QUESTION, rec_type)
        (device, size) = self.ui.get_selected_device()
        if device:
            self.device = device
        if size:
            self.device_size = size

        #advanced questions
        for question in [DUAL_BOOT_QUESTION,
                         DUAL_BOOT_LAYOUT_QUESTION,
                         ACTIVE_PARTITION_QUESTION,
                         DISK_LAYOUT_QUESTION,
                         SWAP_QUESTION,
                         DRIVER_INSTALL_QUESTION,
                         USER_INTERFACE_QUESTION,
                         OIE_QUESTION,
                         RP_FILESYSTEM_QUESTION]:
            answer = self.ui.get_advanced(question)
            if answer:
                self.log("advanced option %s set to %s" % (question, answer))
                self.preseed_config += question + "=" + answer + " "
                if question == RP_FILESYSTEM_QUESTION:
                    self.rp_filesystem = answer
                elif question == DISK_LAYOUT_QUESTION:
                    self.disk_layout = answer
                elif question == DUAL_BOOT_QUESTION:
                    answer = misc.create_bool(answer)
                    self.dual = answer
                elif question == OIE_QUESTION:
                    answer = misc.create_bool(answer)
                    self.oie = answer
                elif question == DUAL_BOOT_LAYOUT_QUESTION:
                    self.dual_layout = answer
            if type(answer) is bool:
                self.preseed_bool(question, answer)
            else:
                self.preseed(question, answer)

        return Plugin.ok_handler(self)

    def report_progress(self, info, percent):
        """Reports to the frontend an update about th progress"""
        self.frontend.debconf_progress_info(info)
        self.frontend.debconf_progress_set(percent)

    def cleanup(self):
        """Do all the real processing for this plugin.
           * This has to be done here because ok_handler won't run in a fully
             automated load, and we need to run all steps in all scenarios
           * Run is the wrong time too because it runs before the user can
             answer potential questions
        """
        rec_type = self.db.get('dell-recovery/recovery_type')

        try:
            # User recovery - need to copy RP
            if rec_type == "automatic":
                self.ui.show_dialog("info")
                self.disable_swap()


                #init progress bar and size thread
                self.frontend.debconf_progress_start(0, 100, "")
                size_thread = ProgressBySize("Copying Files",
                                               "/mnt",
                                               "0")
                size_thread.progress = self.report_progress
                #init builder
                self.rp_builder = RPbuilder(self.device, 
                                            self.device_size,
                                            self.rp_filesystem,
                                            self.mem,
                                            self.dual,
                                            self.dual_layout,
                                            self.disk_layout,
                                            self.efi,
                                            self.preseed_config,
                                            size_thread)
                self.rp_builder.exit = self.exit_ui_loops
                self.rp_builder.status = self.report_progress
                self.rp_builder.start()
                self.enter_ui_loop()
                self.rp_builder.join()
                if self.rp_builder.exception:
                    self.handle_exception(self.rp_builder.exception)
                self.boot_rp()

            # User recovery - resizing drives
            elif rec_type == "interactive":
                self.ui.show_dialog("forward")
                self.unset_drive_preseeds()

            # Factory install, and booting from RP
            else:
                self.sleep_network()
                self.disable_swap()
                self.test_oie()
                self.clean_recipe()
                self.remove_extra_partitions()
                self.explode_utility_partition()
                self.explode_sdr()
                self.install_grub()
        except Exception, err:
            #For interactive types of installs show an error then reboot
            #Otherwise, just reboot the system
            if rec_type == "automatic" or rec_type == "interactive" or \
               ('UBIQUITY_DEBUG' in os.environ and 'UBIQUITY_ONLY' in os.environ):
                self.handle_exception(err)
            self.cancel_handler()

        #translate languages
        self.ui.controller.translate(just_me=False, not_me=True, reget=True)
        Plugin.cleanup(self)

    def cancel_handler(self):
        """Called when we don't want to perform recovery'"""
        misc.execute_root('reboot')

    def handle_exception(self, err):
        """Handle all exceptions thrown by any part of the application"""
        self.log(str(err))
        self.ui.show_dialog("exception", err)

############################
# RP Builder Worker Thread #
############################
class RPbuilder(Thread):
    """The recovery partition builder worker thread"""
    def __init__(self, device, size, rp_type, mem, dual, dual_layout, disk_layout, efi, preseed_config, sizing_thread):
        self.device = device
        self.device_size = size
        self.rp_type = rp_type
        self.mem = mem
        self.dual = dual
        self.dual_layout = dual_layout
        self.disk_layout = disk_layout
        self.efi = efi
        self.preseed_config = preseed_config
        self.exception = None
        self.file_size_thread = sizing_thread
        self.xml_obj = BTOxml()
        Thread.__init__(self)

    def build_rp(self, cushion=300):
        """Copies content to the recovery partition using a parted wrapper.

           This might be better implemented in python-parted or parted_server/partman,
           but those would require extra dependencies, and are generally more complex
           than necessary for what needs to be accomplished here."""

        white_pattern = re.compile('.')

        #Things we know ahead of time will cause us to error out
        if self.disk_layout == 'gpt':
            if self.dual:
                raise RuntimeError, ("Dual boot is not yet supported when configuring the disk as GPT.")
        elif self.disk_layout == 'msdos':
            pass
        else:
            raise RuntimeError, ("Unsupported disk layout: %s" % self.disk_layout)

        #Check if we are booted from same device as target
        mounted_device = find_boot_device()
        if self.device in mounted_device:
            raise RuntimeError, ("Attempting to install to the same device as booted from.\n\
You will need to clear the contents of the recovery partition\n\
manually to proceed.")

        #Adjust recovery partition type to something parted will recognize
        if self.rp_type == TYPE_NTFS or \
           self.rp_type == TYPE_NTFS_RE:
            self.rp_type = 'ntfs'
        elif self.rp_type == TYPE_VFAT or \
             self.rp_type == TYPE_VFAT_LBA:
            self.rp_type = 'fat32'
        else:
            raise RuntimeError, ("Unsupported recovery partition filesystem: %s" % self.rp_type)

        #Default partition numbers
        up_part   = STANDARD_UP_PARTITION
        rp_part   = STANDARD_RP_PARTITION
        grub_part = STANDARD_RP_PARTITION

        #Calculate RP size
        rp_size = magic.white_tree("size", white_pattern, CDROM_MOUNT)
        #in mbytes
        rp_size_mb = (rp_size / 1000000) + cushion

        # Build new partition table
        command = ('parted', '-s', self.device, 'mklabel', self.disk_layout)
        result = misc.execute_root(*command)
        if result is False:
            raise RuntimeError, ("Error creating new partition table %s on %s" % (self.disk_layout, self.device))

        self.status("Creating Partitions", 1)
        if self.disk_layout == 'msdos':
            #Create an MBR
            path = '/usr/share/dell/up/mbr.bin'
            if os.path.exists(path):
                pass
            elif os.path.exists('/usr/lib/syslinux/mbr.bin'):
                path = '/usr/lib/syslinux/mbr.bin'
            else:
                raise RuntimeError, ("Missing both DRMK and syslinux MBR")
            with open(path, 'rb') as mbr:
                with misc.raised_privileges():
                    with open(self.device, 'wb') as out:
                        out.write(mbr.read(440))

            #Utility partition files (tgz/zip)#
            up_size = 33

            #Utility partition image (dd)#
            for fname in magic.UP_FILENAMES:
                if 'img' in fname and os.path.exists(os.path.join(CDROM_MOUNT, fname)):
                    #in a string
                    up_size = magic.fetch_output(['gzip', '-lq', os.path.join(CDROM_MOUNT, fname)])
                    #in bytes
                    up_size = float(up_size.split()[1])
                    #in mbytes
                    up_size = 1 + (up_size / 1000000)

            #Build UP
            command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpartfs', 'primary', 'fat16', '1', str(up_size))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error creating new %s mb utility partition on %s" % (up_size, self.device))

            with misc.raised_privileges():
                #parted marks it as w95 fat16 (LBA).  It *needs* to be type 'de'
                data = 't\nde\n\nw\n'
                magic.fetch_output(['fdisk', self.device], data)

                #build the bootsector of the partition
                magic.write_up_bootsector(self.device, up_part)

            #Build RP
            command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', self.rp_type, str(up_size), str(up_size + rp_size_mb))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))

            #Set RP active (bootable)
            command = ('parted', '-s', self.device, 'set', rp_part, 'boot', 'on')
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error setting recovery partition active %s" % (self.device))

            #Dual boot creates more partitions
            if self.dual:
                my_os_part = 5120 #mb
                other_os_part_end = (int(self.device_size) / 1000000) - my_os_part

                commands = [('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'primary', 'ntfs', str(up_size + rp_size_mb), str(other_os_part_end)),
                            ('mkfs.ntfs' , '-f', '-L', 'OS', self.device + '3')]
                if self.dual_layout == 'primary':
                    commands.append(('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'primary', 'fat32', str(other_os_part_end), str(other_os_part_end + my_os_part)))
                    commands.append(('mkfs.msdos', '-n', 'ubuntu'  , self.device + '4'))
                    #Grub needs to be on the 4th partition to kick off the ubuntu install
                    grub_part = '4'
                else:
                    grub_part = '1'
                for command in commands:
                    result = misc.execute_root(*command)
                    if result is False:
                        raise RuntimeError, ("Error building dual boot partitions")

        #GPT Layout
        elif self.disk_layout == 'gpt':
            #In GPT we don't have a UP, but instead a BIOS grub partition
            up_part = ''
            if self.efi:
                grub_size = 50
                commands = [('parted', '-a', 'minimal', '-s', self.device, 'mkpartfs', 'primary', 'fat16', '0', str(grub_size)),
                            ('parted', '-s', self.device, 'set', '1', 'boot', 'on')]
            else:
                grub_size = 1.5
                commands = [('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'biosboot', '0', str(grub_size)),
                            ('parted', '-s', self.device, 'set', '1', 'bios_grub', 'on')]
            for command in commands:
                result = misc.execute_root(*command)
                if result is False:
                    if self.efi:
                        raise RuntimeError, ("Error creating new %s mb EFI boot partition on %s" % (grub_size, self.device))
                    else:
                        raise RuntimeError, ("Error creating new %s mb grub partition on %s" % (grub_size, self.device))

            #GPT Doesn't support active partitions, so we must install directly to the disk rather than
            #partition
            grub_part = ''

            #Build RP
            command = ('parted', '-a', 'minimal', '-s', self.device, 'mkpart', self.rp_type, self.rp_type, str(grub_size), str(rp_size_mb + grub_size))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))

        #Build RP filesystem
        self.status("Formatting Partitions", 2)
        if self.rp_type == 'fat32':
            command = ('mkfs.msdos', '-n', 'install', self.device + rp_part)
        elif self.rp_type == 'ntfs':
            command = ('mkfs.ntfs', '-f', '-L', 'RECOVERY', self.device + rp_part)
        result = misc.execute_root(*command)
        if result is False:
            raise RuntimeError, ("Error creating %s filesystem on %s%s" % (self.rp_type, self.device, rp_part))

        #Mount RP
        mount = misc.execute_root('mount', self.device + rp_part, '/mnt')
        if mount is False:
            raise RuntimeError, ("Error mounting %s%s" % (self.device, rp_part))

        #Update status and start the file size thread
        self.file_size_thread.reset_write(rp_size)
        self.file_size_thread.set_scale_factor(85)
        self.file_size_thread.set_starting_value(2)
        self.file_size_thread.start()

        #Copy RP Files
        with misc.raised_privileges():
            magic.white_tree("copy", white_pattern, CDROM_MOUNT, '/mnt')

        self.file_size_thread.join()

        #If dual boot, mount the proper /boot partition first
        if self.dual:
            mount = misc.execute_root('mount', self.device + grub_part, '/mnt')
            if mount is False:
                raise RuntimeError, ("Error mounting %s%s" % (self.device, grub_part))

        #find uuid of drive
        with misc.raised_privileges():
            blkid = magic.fetch_output(['blkid', self.device + rp_part, "-p", "-o", "udev"]).split('\n')
            for item in blkid:
                if item.startswith('ID_FS_UUID'):
                    uuid = item.split('=')[1]
                    break

        #read in any old seed
        seed = os.path.join('/mnt', 'preseed', 'dell-recovery.seed')
        keys = magic.parse_seed(seed)

        #process the new options
        for item in self.preseed_config.split():
            if '=' in item:
                key, value = item.split('=')
                keys[key] = value

        #write out a dell-recovery.seed configuration file
        with misc.raised_privileges():
            if not os.path.isdir(os.path.join('/mnt', 'preseed')):
                os.makedirs(os.path.join('/mnt', 'preseed'))
            magic.write_seed(seed, keys)

        #Check for a grub.cfg - replace as necessary
        files = {'recovery_partition.cfg': 'grub.cfg',
                 'common.cfg' : 'common.cfg'} 
        for item in files:
            if os.path.exists(os.path.join('/mnt', 'boot', 'grub', files[item])):
                with misc.raised_privileges():
                    shutil.move(os.path.join('/mnt', 'boot', 'grub', files[item]),
                                os.path.join('/mnt', 'boot', 'grub', files[item]) + '.old')

            with misc.raised_privileges():
                
                magic.process_conf_file('/usr/share/dell/grub/' + item, \
                                   os.path.join('/mnt', 'boot', 'grub', files[item]),\
                                   uuid, rp_part)
                #Allow these to be invoked from a recovery solution launched by the BCD.
                if self.dual:
                    shutil.copy(os.path.join('/mnt', 'boot', 'grub', files[item]), \
                                os.path.join('/tmp', files[item]))

        #Install grub
        self.status("Installing GRUB", 88)
        if self.efi:
            with misc.raised_privileges():
                os.makedirs('/mnt/efi')
            mount = misc.execute_root('mount', self.device + STANDARD_EFI_PARTITION, '/mnt/efi')
            if mount is False:
                raise RuntimeError, ("Error mounting %s%s" % (self.device, STANDARD_EFI_PARTITION))
            grub = misc.execute_root('grub-install', '--force')
            if grub is False:
                raise RuntimeError, ("Error installing grub")
            misc.execute_root('umount', '/mnt/efi')
        else:
            grub = misc.execute_root('grub-install', '--root-directory=/mnt', '--force', self.device + grub_part)
            if grub is False:
                raise RuntimeError, ("Error installing grub to %s%s" % (self.device, STANDARD_RP_PARTITION))

        #dual boot needs primary #4 unmounted
        if self.dual:
            misc.execute_root('umount', '/mnt')
            self.status("Building G2LDR", 90)
            #build g2ldr
            magic.create_g2ldr('/', '/mnt', '')
            if not os.path.isdir(os.path.join('/mnt', 'boot', 'grub')):
                os.makedirs(os.path.join('/mnt', 'boot', 'grub'))
            for item in files:
                shutil.copy(os.path.join('/tmp', files[item]), \
                            os.path.join('/mnt', 'boot', 'grub', files[item]))


        #Build new UUID
        if int(self.mem) >= 1: #GB
            if os.path.isdir(ISO_MOUNT):
                syslog.syslog("Skipping UUID generation - booted from ISO image.")
            else:
                self.status("Regenerating UUID / initramfs", 90)
                with misc.raised_privileges():
                    magic.create_new_uuid(os.path.join(CDROM_MOUNT, 'casper'),
                            os.path.join(CDROM_MOUNT, '.disk'),
                            os.path.join('/mnt', 'casper'),
                            os.path.join('/mnt', '.disk'))
        else:
            #The new UUID just fixes the installed-twice-on-same-system scenario
            #most users won't need that anyway so it's just nice to have
            syslog.syslog("Skipping casper UUID build due to low memory")

        #update bto.xml
        path = os.path.join(CDROM_MOUNT, 'bto.xml')
        if os.path.exists(path):
            self.xml_obj.load_bto_xml(path)
        bto_version = self.xml_obj.fetch_node_contents('iso')
        bto_date = self.xml_obj.fetch_node_contents('date')
        with misc.raised_privileges():
            dr_version = magic.check_version('dell-recovery')
            ubi_version = magic.check_version('ubiquity')
            self.xml_obj.replace_node_contents('bootstrap', dr_version)
            self.xml_obj.replace_node_contents('ubiquity' , ubi_version)
            if os.path.exists('/var/log/syslog'):
                with open('/var/log/syslog', 'r') as rfd:
                    self.xml_obj.replace_node_contents('syslog', rfd.read())
            if os.path.exists('/var/log/installer/debug'):
                with open('/var/log/installer/debug', 'r') as rfd:
                    self.xml_obj.replace_node_contents('debug', rfd.read())
            if not bto_version:
                self.xml_obj.replace_node_contents('iso', '[native]')
            if not bto_date:
                with open(os.path.join(CDROM_MOUNT, '.disk', 'info')) as rfd:
                    line = rfd.readline().strip()
                date = line.split()[len(line.split())-1]
                self.xml_obj.replace_node_contents('date', date)
            self.xml_obj.write_xml('/mnt/bto.xml')
        misc.execute_root('umount', '/mnt')

    def exit(self):
        """Function to request the builder thread to close"""
        pass

    def status(self, info, percent):
        """Stub function for passing data back up"""
        pass

    def run(self):
        """Start the RP builder thread"""
        try:
            self.build_rp()
        except Exception, err:
            self.exception = err
        self.exit()

####################
# Helper Functions #
####################
def find_boot_device():
    """Finds the device we're booted from'"""
    with open('/proc/mounts', 'r') as mounts:
        for line in mounts.readlines():
            if ISO_MOUNT in line:
                mounted_device = line.split()[0]
                break
            if CDROM_MOUNT in line:
                found = line.split()[0]
                if not 'loop' in found:
                    mounted_device = line.split()[0]
                    break
    return mounted_device

def reboot_machine(objpath):
    """Reboots the machine"""
    reboot_cmd = '/sbin/reboot'
    reboot = misc.execute_root(reboot_cmd)
    if reboot is False:
        raise RuntimeError, ("Reboot failed from %s" % str(objpath))

def find_item_iterator(combobox, value, column = 0):
    """Searches a combobox for a value and returns the iterator that matches"""
    model = combobox.get_model()
    iterator = model.get_iter_first()
    while iterator is not None:
        if value == model.get_value(iterator, column):
            break
        iterator = model.iter_next(iterator)
    return iterator

def find_n_set_iterator(combobox, value, column = 0):
    """Searches a combobox for a value, and sets the iterator to that value if
       it's found"""
    iterator = find_item_iterator(combobox, value, column)
    if iterator is not None:
        combobox.set_active_iter(iterator)

###########################################
# Commands Processed During Install class #
###########################################
class Install(InstallPlugin):
    """The install time dell-bootstrap ubiquity plugin"""
    
    def __init__(self, frontend, db=None, ui=None):
        self.progress = None
        self.target = None
        InstallPlugin.__init__(self, frontend, db, ui)

    def find_unconditional_debs(self):
        '''Finds any debs from debs/main that we want unconditionally installed
           (but ONLY the latest version on the media)'''
        import apt_inst
        import apt_pkg

        def parse(fname):
            """ read a deb """
            control = apt_inst.debExtractControl(open(fname))
            sections = apt_pkg.TagSection(control)
            if sections.has_key("Modaliases"):
                modaliases = sections["Modaliases"]
            else:
                modaliases = ''
            return (sections["Architecture"], sections["Package"], modaliases)

        #process debs/main
        to_install = []
        my_arch = magic.fetch_output(['dpkg', '--print-architecture']).strip()
        for top in [ISO_MOUNT, CDROM_MOUNT]:
            repo = os.path.join(top, 'debs', 'main')
            if os.path.isdir(repo):
                for fname in os.listdir(repo):
                    if '.deb' in fname:
                        arch, package, modaliases = parse(os.path.join(repo, fname))
                        if not modaliases and (arch == "all" or arch == my_arch):
                            to_install.append(package)

        #These aren't in all images, but desirable if available
        to_install.append('dkms')
        to_install.append('adobe-flashplugin')

        return to_install

    def remove_ricoh_mmc(self):
        '''Removes the ricoh_mmc kernel module which is known to cause problems
           with MDIAGS'''
        lsmod = magic.fetch_output('lsmod').split('\n')
        for line in lsmod:
            if line.startswith('ricoh_mmc'):
                misc.execute('rmmod', line.split()[0])

    def enable_oem_config(self):
        '''Enables OEM config on the target'''
        oem_dir = os.path.join(self.target, 'var/lib/oem-config')
        if not os.path.exists(oem_dir):
            os.makedirs(oem_dir)
        with open(os.path.join(oem_dir, 'run'), 'w'):
            pass

    def propagate_kernel_parameters(self):
        '''Copies in kernel command line parameters that were needed during
           installation'''
        extra = magic.find_extra_kernel_options()
        new = ''
        for item in extra.split():
            if not 'debian-installer/'                in item and \
               not 'console-setup/'                   in item and \
               not 'locale='                          in item and \
               not 'BOOT_IMAGE='                      in item and \
               not 'iso-scan/'                        in item and \
               not 'ubiquity'                         in item:
                new += '%s ' % item
        extra = new.strip()

        grubf = os.path.join(self.target, 'etc/default/grub')
        if extra and os.path.exists(grubf):
            #read/write new grub
            with open(grubf, 'r') as rfd:
                default_grub = rfd.readlines()
            with open(grubf, 'w') as wfd:
                for line in default_grub:
                    if 'GRUB_CMDLINE_LINUX_DEFAULT' in line:
                        line = line.replace('GRUB_CMDLINE_LINUX_DEFAULT="', \
                                      'GRUB_CMDLINE_LINUX_DEFAULT="%s ' % extra)
                    wfd.write(line)
            from ubiquity import install_misc
            install_misc.chrex(self.target, 'update-grub')

    def remove_unwanted_drivers(self):
        '''Removes drivers that were preseeded to not used for postinstall'''
        drivers = ''

        try:
            drivers = self.progress.get(DRIVER_INSTALL_QUESTION).split(',')
        except debconf.DebconfError:
            pass

        if len(drivers) > 0:
            for driver in drivers:
                if driver:
                    with open (os.path.join(self.target, '/usr/share/jockey/modaliases/', driver), 'w') as wfd:
                        wfd.write('reset %s\n' % driver)

    def mark_upgrades(self):
        '''Mark packages that can upgrade to upgrade during install'''
        cache = Cache()
        to_install = []
        for key in cache.keys():
            if cache[key].is_upgradable:
                to_install.append(key)
        del cache
        return to_install


    def g2ldr(self):
        '''Builds a grub2 based loader to allow booting a logical partition'''
        #Mount the disk
        if os.path.exists(ISO_MOUNT):
            mount = ISO_MOUNT
        else:
            mount = CDROM_MOUNT
            misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)

        magic.create_g2ldr(self.target, mount, self.target)

        #Don't re-run installation
        if os.path.exists(os.path.join(mount, 'grub', 'grub.cfg')):
            os.unlink(os.path.join(mount, 'grub', 'grub.cfg'))

    def wake_network(self):
        """Wakes the network back up"""
        bus = dbus.SystemBus()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            backend_iface = dbus.Interface(bus.get_object(magic.DBUS_BUS_NAME, '/RecoveryMedia'), magic.DBUS_INTERFACE_NAME)
            backend_iface.force_network(True)
            backend_iface.request_exit()
        except Exception:
            pass


    def install(self, target, progress, *args, **kwargs):
        '''This is highly dependent upon being called AFTER configure_apt
        in install.  If that is ever converted into a plugin, we'll
        have some major problems!'''
        genuine = magic.check_vendor()
        if not genuine:
            raise RuntimeError, ("This recovery media requires Dell Hardware.")

        self.target = target
        self.progress = progress

        utility_part,  rec_part  = magic.find_partitions('', '')

        from ubiquity import install_misc
        to_install = []
        to_remove  = []

        #Determine if we are doing OOBE
        try:
            if progress.get('oem-config/enable') == 'true':
                self.enable_oem_config()
        except debconf.DebconfError:
            pass

        #The last thing to do is set an active partition
        #This happens at the end of success command
        active = ''
        try:
            active = progress.get(ACTIVE_PARTITION_QUESTION)
        except debconf.DebconfError:
            pass
        try:
            layout = progress.get(DISK_LAYOUT_QUESTION)
        except debconf.DebconfError:
            layout = 'msdos'

        if active.isdigit():
            disk = progress.get('partman-auto/disk')
            with open('/tmp/set_active_partition', 'w') as wfd:
                #If we have an MBR, 
                if layout == 'msdos':
                    #we use the active partition bit in it
                    wfd.write('sfdisk -A%s %s\n' % (active, disk))

                    #in factory process if we backed up an MBR, that would have already
                    #been restored.
                    if not os.path.exists(os.path.join(CDROM_MOUNT, 'factory', 'mbr.bin')):
                        #we don't necessarily know how we booted
                        #test the md5 of the MBR to match DRMK or syslinux
                        #if they don't match, rewrite MBR
                        with misc.raised_privileges():
                            with open(disk, 'rb') as rfd:
                                disk_mbr = rfd.read(440)
                        path = '/usr/share/dell/up/mbr.bin'
                        if not os.path.exists(path):
                            path = '/usr/lib/syslinux/mbr.bin'
                        if not os.path.exists(path):
                            raise RuntimeError, ("Missing DRMK and syslinux MBR")
                        with open(path, 'rb') as rfd:
                            file_mbr = rfd.read(440)
                        if hashlib.md5(file_mbr).hexdigest() != hashlib.md5(disk_mbr).hexdigest():
                            self.debug("%s: MBR of disk is invalid, rewriting" % NAME)
                            with misc.raised_privileges():
                                with open(disk, 'wb') as wfd:
                                    wfd.write(file_mbr)

                #If we have GPT, we need to go down other paths
                elif layout == 'gpt':
                    #If we're booted in EFI mode, then the OS has already set
                    #the correct Bootnum active
                    if os.path.isdir('/proc/efi') or os.path.isdir('/sys/firmware/efi'):
                        pass
                    #If we're not booted to EFI mode, but using GPT,
                    else:
                        #See https://bugs.launchpad.net/ubuntu/+source/partman-partitioning/+bug/592813
                        #for why we need to have this workaround in the first place
                        result = misc.execute_root('parted', '-s', disk, 'set', active, 'bios_grub', 'on')
                        if result is False:
                            raise RuntimeError, ("Error working around bug 592813.")
                        
                        wfd.write('grub-install --no-floppy %s\n' % disk)
            os.chmod('/tmp/set_active_partition', 0755)

        #if we are loop mounted, make sure the chroot knows it too
        if os.path.isdir(ISO_MOUNT):
            os.makedirs(os.path.join(self.target, ISO_MOUNT.lstrip('/')))
            misc.execute_root('mount', '--bind', ISO_MOUNT, os.path.join(self.target, ISO_MOUNT.lstrip('/')))

        #Fixup pool to only accept stuff on /cdrom or /isodevice
        # - This is reverted during SUCCESS_SCRIPT
        # - Might be in livefs already, but we always copy in in case there was an udpate
        pool_cmd = '/usr/share/dell/scripts/pool.sh'
        shutil.copy(pool_cmd, os.path.join(self.target, 'tmp', os.path.basename(pool_cmd)))
        install_misc.chrex(self.target, os.path.join('/tmp', os.path.basename(pool_cmd)))

        #Stuff that is installed on all configs without fish scripts
        to_install += self.find_unconditional_debs()

        #Query Dual boot or not
        try:
            dual = misc.create_bool(progress.get(DUAL_BOOT_QUESTION))
        except debconf.DebconfError:
            dual = False

        if dual:
            #we don't want EULA or dell-recovery in dual mode
            for package in ['dell-eula', 'dell-recovery']:
                try:
                    to_install.remove(package)
                    to_remove.append(package)
                except ValueError:
                    continue
            #build grub2 loader for logical partitions when necessary
            try:
                layout = progress.get(DUAL_BOOT_LAYOUT_QUESTION)
                if layout == 'logical':
                    self.g2ldr()
            except debconf.DebconfError:
                raise RuntimeError, ("Error determining dual boot layout.")

        #install dell-recovery in non dual mode only if there is an RP
        elif rec_part:
            to_install.append('dell-recovery')

            #block os-prober in grub-installer
            os.rename('/usr/bin/os-prober', '/usr/bin/os-prober.real')
            #don't allow OS prober to probe other drives in single OS install
            with open(os.path.join(self.target, 'etc/default/grub'), 'r') as rfd:
                default_grub = rfd.readlines()
            with open(os.path.join(self.target, 'etc/default/grub'), 'w') as wfd:
                found = False
                for line in default_grub:
                    if line.startswith("GRUB_DISABLE_OS_PROBER="):
                        line = "GRUB_DISABLE_OS_PROBER=true\n"
                        found = True
                    wfd.write(line)
                if not found:
                    wfd.write("GRUB_DISABLE_OS_PROBER=true\n")


        #if oie, pass on information to post install
        try:
            oie = misc.create_bool(progress.get(OIE_QUESTION))
        except debconf.DebconfError:
            oie = False
        if oie:
            with open('/tmp/oie', 'w') as wfd:
                pass

        to_install += self.mark_upgrades()

        self.remove_unwanted_drivers()
                    
        self.remove_ricoh_mmc()

        self.propagate_kernel_parameters()

        self.wake_network()

        install_misc.record_installed(to_install)
        install_misc.record_removed(to_remove)

        return InstallPlugin.install(self, target, progress, *args, **kwargs)

