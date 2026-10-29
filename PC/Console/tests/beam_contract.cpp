// [波束控制新增] 独立几何/量化/寄存器顺序断言与真实 TCP 控制契约。
// register fixture 只模拟寄存器，不冒充实板验证。
#include "beam_control.hpp"
#include "capture_mode.hpp"
#include <cassert>
#include <iostream>
#include <map>
#include <thread>
using namespace aoa_app;
using aoa_console::Json;
Options options() {
    Options o;
    const double d=light_speed/433e6/2;
    for(int i=0;i<6;++i) o.antenna[i].x=(i<3?i*d:(i-1)*d+.5)-2*d-.25;
    return o;
}
struct Registers {
    std::map<unsigned,unsigned> values{{0xc0,current_pl_version}};
    std::vector<std::uint64_t> latched;
    bool bad=false,bad_enable=false;
    unsigned read(unsigned a) {
        if(bad&&a==0x24) return 0;
        if(bad_enable&&a==0x20&&values[a]==1) return 0;
        return values[a];
    }
    void write(unsigned a,unsigned v) {
        if(a==0x20&&v==1) latched.push_back((std::uint64_t(values[0x28])<<32)|values[0x24]);
        if(a==0x24||a==0x28) assert(values[0x20]==0);
        values[a]=v;
    }
    void apply(const BeamWeights& w) {
        write_beam_registers(w,[&](unsigned a,unsigned v){write(a,v);},
            [&](unsigned a){return read(a);},[]{});
    }
};
void tests() {
    const auto o=options();
    // Online locate and identify share four snapshots; explicit offline counts remain unchanged.
    auto scheduled=o;scheduled.ipc_enabled=false;schedule_capture_rounds(scheduled);
    assert(scheduled.capture_rounds==npu_rounds && scheduled.sample_rate==o.sample_rate);
    scheduled.ipc_enabled=true;schedule_capture_rounds(scheduled);
    assert(scheduled.capture_rounds==npu_rounds);
    scheduled.fft_sources={"a","b","c","d"};scheduled.ipc_enabled=false;
    schedule_capture_rounds(scheduled);assert(scheduled.capture_rounds==npu_rounds);
    scheduled.fft_sources.clear();scheduled.dry_run=true;
    schedule_capture_rounds(scheduled);assert(scheduled.capture_rounds==npu_rounds);
    auto w=beam_weights(o,"manual",31);
    const double k=2*pi*o.rf_hz/light_speed,theta=31*pi/180;
    std::complex<double> sum{};
    for(int ch=0;ch<6;++ch) {
        // 独立生成入射波，应用计算权值后应同相叠加。
        const auto wave=std::polar(1.,k*(o.antenna[ch].x-o.antenna[0].x)*std::sin(theta));
        sum+=wave*std::polar(1.,w.phase_rad[ch]);
        assert((w.words[ch]>>61)==unsigned(ch));
    }
    assert(std::abs(std::abs(sum)-6)<1e-10);
    sum={};
    auto signed24=[](std::uint64_t bits) {
        const auto u=static_cast<std::int32_t>(bits&0xffffff);
        return double((u&0x800000)?u-0x1000000:u)/8388608.;
    };
    for(int ch=0;ch<6;++ch)
        sum+=std::polar(1.,k*(o.antenna[ch].x-o.antenna[0].x)*std::sin(theta))*
             std::complex<double>(signed24(w.words[ch]),signed24(w.words[ch]>>24));
    assert(std::abs(sum-std::complex<double>(6,0))<1e-6);
    assert(std::abs(std::remainder(w.phase_rad[3]-w.phase_rad[2],2*pi)+k*.5*std::sin(theta))<1e-10);
    // 不同组内/组间间距、非零 y、相位校准均实际参与。
    auto shifted=o;shifted.antenna[4].y=.13;shifted.antenna[4].correction=std::polar(1.,.4);
    auto sw=beam_weights(shifted,"manual",31);
    assert(std::abs(sw.phase_rad[4]-w.phase_rad[4])>.01);
    auto nf=beam_weights(o,"follow",0,.2,2.0);
    sum={};const double ref=std::hypot(.2-o.antenna[0].x,2.);
    for(int ch=0;ch<6;++ch)
        sum+=std::polar(1.,-k*(std::hypot(.2-o.antenna[ch].x,2.)-ref)+nf.phase_rad[ch]);
    assert(std::abs(std::abs(sum)-6)<1e-10);
    assert((beam_word(0,0)&0xffffff)==0x7fffff);
    assert(((beam_word(5,-pi/2)>>24)&0xffffff)==0x800000);
    Registers regs;
    for(int round=0;round<4;++round) regs.apply(w);
    assert(regs.latched.size()==24);
    for(int i=0;i<24;++i) assert(regs.latched[i]==w.words[i%6]);
    regs.values[0xc0]=0x20260922;bool rejected=false;
    try {regs.apply(w);} catch(const std::exception&) {rejected=true;}
    assert(rejected&&regs.latched.size()==24);
    auto negative=o;negative.phase_sign=-1;
    auto nw=beam_weights(negative,"manual",31);
    for(int ch=0;ch<6;++ch)
        assert(std::abs(nw.phase_rad[ch]+w.phase_rad[ch])<1e-10);
    regs.values[0xc0]=current_pl_version;regs.bad=true;rejected=false;
    try {regs.apply(w);} catch(const std::exception&) {rejected=true;}
    assert(rejected&&regs.latched.size()==24);
    regs.bad=false;regs.bad_enable=true;rejected=false;
    try {regs.apply(w);} catch(const std::exception&) {rejected=true;}
    assert(rejected&&regs.values[0x20]==0&&regs.latched.size()==25);
    // [自动跟踪核验] 最强组合在后向时必须排除，不能通过关闭几何门限获得目标。
    Analysis a;a.complete=true;a.status="ok";
    for(int g=0;g<2;++g) {
        GroupEstimate ge;ge.id=g;ge.has_best=true;ge.energy=1;ge.residual=.01;
        ge.angle=g?std::vector<double>{-10,30}:std::vector<double>{-40,10};
        ge.amplitude_rms={g?1.:.05,g?.1:1.};ge.active={true,true};a.groups.push_back(ge);
    }
    auto best=strongest_beam_candidate(o,a);
    assert(best.fix.valid&&best.paths[0]==1&&best.paths[1]==0);
    assert(std::abs(best.energy_share-(1/1.0025)*(1/1.01))<1e-12);
    a.groups[0].ambiguous=true;assert(!strongest_beam_candidate(o,a).fix.valid);
    a.groups[0].ambiguous=false;a.groups[1].residual=.2;
    assert(!strongest_beam_candidate(o,a).fix.valid);
    a.groups[1].residual=.01;a.groups[0].active[1]=false;
    assert(!strongest_beam_candidate(o,a).fix.valid);
    a.groups[0].active[1]=true;a.groups[0].angle={-40,-30};
    assert(!strongest_beam_candidate(o,a).fix.valid);
    std::cout<<"beam math/register contract PASS\n";
}
int main(int argc,char**) {
    tests();
    if(argc==1) return 0;
    auto o=options();aoa_console::Publisher p("aoa");BeamController controller(true);
    // 模拟已知版本之后 ADC 在到达 PL 之前失败；不能误报“请更新旧位流”。
    CaptureState initial;initial.pl_version=current_pl_version;
    controller.finish(o,CaptureBatch{},initial,Analysis{},p);
    initial.pl_version=0;
    controller.finish(o,CaptureBatch{},initial,Analysis{},p);
    assert(controller.state()["pl_version"]==current_pl_version);
    const char* fault_env=std::getenv("BEAM_TEST_FAULT");
    const std::string fault=fault_env?fault_env:"";int followed=0;
    if(fault=="nonmodel_rate") {o.sample_rate=50e6;o.ipc_enabled=false;}
    for(int i=0;i<120;++i) {
        controller.prepare(o,p);
        schedule_capture_rounds(o);
        CaptureState c;c.complete=true;c.pl_version=current_pl_version;c.beam_registers_verified=o.beam.enabled;
        CaptureBatch batch;batch.completed=o.capture_rounds;batch.rounds.assign(o.capture_rounds,c);batch.run_id="beam-test-"+std::to_string(i);
        Analysis a;a.complete=true;a.status="ok";
        for(int g=0;g<2;++g) {
            GroupEstimate group;group.id=g;group.has_best=true;group.energy=1;group.residual=.01;
            group.angle=g?std::vector<double>{-28,-8}:std::vector<double>{12,35};
            group.amplitude_rms=g?std::vector<double>{1,.2}:std::vector<double>{.4,1};
            group.active={true,true};a.groups.push_back(group);
        }
        if(controller.state()["tracking"].get<bool>()) ++followed;
        if(followed>=3) {
            if(fault=="loss") {a.complete=false;a.status="incomplete";}
            if(fault=="ambiguous") a.groups[0].angle={34,36};
            if(fault=="reorder")
                for(auto& g:a.groups) std::reverse(g.angle.begin(),g.angle.end());
            if(fault=="auto_switch") a.groups[0].amplitude_rms={2,1};
            if(fault=="auto_hold" && followed<8) a.groups[0].ambiguous=true;
        }
        if(o.beam.enabled&&fault=="capture") {
            batch.completed=o.capture_rounds-1;batch.rounds.resize(batch.completed);c.error="injected capture failure";
        }
        if(o.beam.enabled) {Registers regs;regs.apply(o.beam);}
        controller.finish(o,batch,c,a,p);
        auto b=controller.state();b["test_fixture"]=true;b["test_capture_rounds"]=o.capture_rounds;
        p.publish({{"type","heartbeat"},{"run_id",""},{"beam",b}});
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}
