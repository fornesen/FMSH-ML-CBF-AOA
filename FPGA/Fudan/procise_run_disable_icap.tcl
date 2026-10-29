set root [file dirname [file normalize [info script]]]
set_device JFMQL30TAI676H
increment_bit_cfg_v3 [file join $root fpai_demo_vivado.runs impl_1 ai7030_edif_top.bit] [file join $root fpai_demo_vivado.runs impl_1 ai7030_edif_top_disable_icap.bit] -disable_icap
