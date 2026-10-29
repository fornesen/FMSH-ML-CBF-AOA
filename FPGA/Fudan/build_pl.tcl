set_param general.maxThreads 2
if {$argc > 1} {error "Pass zero or one project.xpr path"}
set source_root [file dirname [file normalize [info script]]]
set project_file [file join $source_root fpai_demo_vivado.xpr]
if {$argc == 1} {set project_file [file normalize [lindex $argv 0]]}
set project_dir [file dirname $project_file]
open_project $project_file
source [file join $project_dir tools integrate_fft_window.tcl]
set report_dir [file join $project_dir reports]
file mkdir $report_dir
report_ip_status -file [file join $report_dir ip_status.txt]
reset_run synth_1
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    error "Synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
launch_runs impl_1 -to_step route_design -jobs 1
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ni {"route_design Complete!" "route_design Complete, Failed Timing!"}} {
    error "Implementation failed: [get_property STATUS [get_runs impl_1]]"
}
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $report_dir timing_summary.txt]
report_utilization -file [file join $report_dir utilization.txt]
report_drc -file [file join $report_dir drc.txt]
set timing_ok 1
foreach kind {max min} {
    set paths [get_timing_paths -delay_type $kind -max_paths 1]
    if {[llength $paths] != 1} {error "No $kind timing path"}
    set slack [get_property SLACK $paths]
    puts "WINDOW_FULL_TIMING_${kind}: $slack ns"
    if {$slack < 0} {set timing_ok 0}
}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {error "DRC errors"}
if {!$timing_ok} {error "Timing has not converged; review before generating deployment bitstream"}
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 1
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {
    error "Bitstream failed: [get_property STATUS [get_runs impl_1]]"
}
puts "WINDOW_FULL_BUILD_PASS: [get_property DIRECTORY [get_runs impl_1]]"
close_project
exit
