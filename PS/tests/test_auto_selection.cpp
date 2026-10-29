#ifdef NDEBUG
#undef NDEBUG
#endif
#include "beam_control.hpp"
#include <cassert>
#include <iostream>
#include <limits>

int main() {
    using namespace aoa_app;
    GroupEstimate group;
    group.angle={-30,60};group.active={true,true};group.amplitude_rms={10,9.99};
    assert(strongest_joint_path(group)==0);
    // The following batch switches immediately, even below the old 3 dB margin.
    group.amplitude_rms={9.99,10};
    assert(strongest_joint_path(group)==1);
    group.angle={60,-30};group.amplitude_rms={10,9.99};
    assert(strongest_joint_path(group)==0);
    group.active[0]=false;
    assert(strongest_joint_path(group)==1);
    group.amplitude_rms[1]=std::numeric_limits<double>::quiet_NaN();
    assert(strongest_joint_path(group)==-1);
    Analysis analysis;analysis.joint=group;
    analysis.joint.amplitude_rms={100,1};analysis.joint.active={true,true};
    analysis.selection.enabled=true;analysis.selection.accepted=true;analysis.selection.path_index=1;
    assert(automatic_joint_path(analysis)==1); // A stronger component must not override the gate.
    analysis.selection.accepted=false;
    assert(automatic_joint_path(analysis)==-1); // Never fall back on a rejected frame.
    const auto state=BeamController(false).state();
    assert(state["path_tracking"]["enabled"]==false);
    assert(state["path_tracking"]["active"]==false);
    assert(state["auto_selection"]=="current_batch_strongest");
    std::cout<<"Automatic selection: current strongest path; no association or hysteresis\n";
}
