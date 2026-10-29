# Volatile JTAG deployment only. Do not alter SD/BOOT.bin or the Fudan FPGA.
set root [file dirname [file normalize [info script]]]
set assets [file normalize [file join $root .. .. assets fpga7100]]
proc ensure_stopped {} {
    if {[catch {stop} message] && ![string match {*Already stopped*} $message]} {error $message}
}
connect -url tcp:127.0.0.1:3121
targets -set -filter {name =~ "ARM*#0"}
ensure_stopped
rst -system
after 1000
targets -set -filter {name =~ "ARM*#0"}
ensure_stopped
fpga -file [file join $assets network_adc.bit]
source [file join $root sdk design_1_wrapper_hw_platform_1 ps7_init.tcl]
ps7_init
ps7_post_config
targets -set -filter {name =~ "ARM*#0"}
# Clear the prior program's MMU/cache/CPU context before entering the new ELF.
rst -processor
dow [file join $assets network.elf]
con
disconnect
puts "ADC_PL_AND_SIX_CHANNEL_PS_DEPLOYED"
exit
