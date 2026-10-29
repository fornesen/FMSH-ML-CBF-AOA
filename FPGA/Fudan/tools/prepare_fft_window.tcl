# Batch entry point: integrate and regenerate the BD, without programming a board.
set root [file dirname [file dirname [file normalize [info script]]]]
open_project [file join $root fpai_demo_vivado.xpr]
source [file join $root tools integrate_fft_window.tcl]
close_project
exit
