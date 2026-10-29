#ifdef NDEBUG
#undef NDEBUG
#endif
#include "v12_joint.hpp"
#include "console_aoa.hpp"
#include <cassert>
#include <iostream>
#include <numeric>

namespace aoa_app {
double bin_hz(std::size_t bin,double rate) {
    return (bin>=fft_length/2?double(bin)-fft_length:double(bin))*rate/4/fft_length;
}
std::uint32_t dds_word(const Options&) {return 0;}
}
using namespace aoa_app;
using Vec=std::array<AoaComplex,channels>;
namespace {
bool close_enough(double a,double b,double scale=1) {return std::abs(a-b)<2e-9*scale;}
Options options() {
    Options o;o.processing_mode="angle6";o.angle_start=-60;o.angle_step=12;o.angle_count=11;
    o.scan_verify=false;o.scan_min_snr_db=-1000;o.rf_hz=445e6;o.console_enabled=true;
    const double x[]={-1.12,-.83,-.44,.47,.89,1.21},y[]={.03,-.04,.02,-.02,.01,.05};
    for(std::size_t c=0;c<channels;++c) {o.antenna[c].x=x[c];o.antenna[c].y=y[c];}
    return o;
}
Vec response(const Options& o,double angle) {
    // Independent scalar steering expression and accumulation order.
    double x=0,y=0;for(auto a:o.antenna) {x+=a.x;y+=a.y;} x/=6;y/=6;
    Vec out{};const auto rad=angle*std::acos(-1.0)/180;
    for(std::size_t c=0;c<6;++c) {
        const double phase=o.phase_sign*2*std::acos(-1.0)*o.rf_hz/light_speed*
            ((o.antenna[c].x-x)*std::sin(rad)+(o.antenna[c].y-y)*std::cos(rad));
        out[c]={std::cos(phase),std::sin(phase)};
    }
    return out;
}
double direct_power(const Options& o,const FftData& f,const std::vector<std::size_t>& bins,double angle) {
    const auto a=response(o,angle);double out=0;
    const auto frames=f.snapshots.empty()?std::vector<std::vector<AoaComplex>>{f.corrected}:f.snapshots;
    for(auto b:bins) for(const auto& t:frames) {
        AoaComplex dot{};for(std::size_t c=0;c<6;++c) dot+=std::conj(a[c])*t[c*fft_length+b];
        out+=std::norm(dot)/36.0/frames.size();
    }
    return out;
}
double qr_loss(const Options& o,const FftData& f,const std::vector<std::size_t>& bins,
               const std::array<std::size_t,3>& ix,int k) {
    std::array<Vec,3> q{};
    for(int p=0;p<k;++p) {
        q[p]=response(o,o.angle_start+ix[p]*o.angle_step);
        for(int pass=0;pass<2;++pass) for(int j=0;j<p;++j) {
            AoaComplex d{};for(std::size_t c=0;c<6;++c)d+=std::conj(q[j][c])*q[p][c];
            for(std::size_t c=0;c<6;++c)q[p][c]-=d*q[j][c];
        }
        double n=0;for(auto z:q[p])n+=std::norm(z);
        if(n<6e-12)return std::numeric_limits<double>::infinity();
        for(auto& z:q[p])z/=std::sqrt(n);
    }
    double loss=0;
    for(auto b:bins) {
        Vec e{};for(std::size_t c=0;c<6;++c)e[c]=f.corrected[c*fft_length+b];
        for(int p=0;p<k;++p) {
            AoaComplex d{};for(std::size_t c=0;c<6;++c)d+=std::conj(q[p][c])*f.corrected[c*fft_length+b];
            for(std::size_t c=0;c<6;++c)e[c]-=q[p][c]*d;
        }
        for(auto z:e)loss+=std::norm(z);
    }
    return loss;
}
FftData signal(const Options& o,double angle,bool varied) {
    FftData f;f.valid.assign(fft_records,1);auto a=response(o,angle);
    for(std::size_t t=0;t<4;++t) {
        std::vector<AoaComplex> frame(fft_records,AoaComplex(0,0));
        for(std::size_t c=0;c<6;++c)for(auto b:std::vector<std::size_t>{11,17}) {
            const auto amp=std::polar(1.0+.3*t+.02*b,.37*t+.13*b);
            frame[c*fft_length+b]=a[c]*amp;
            if(varied)frame[c*fft_length+b]=(1+.19*c)*frame[c*fft_length+b]+
                std::polar(.09*(c+1),.62*c+.9*t+.21*b);
        }
        f.snapshots.push_back(std::move(frame));
    }
    f.raw=f.corrected=f.snapshots.back();return f;
}
void pools() {
    auto o=options();auto f=signal(o,17.237,true);
    // Two additional sources ensure a unique residual ranking in every order.
    for(std::size_t c=0;c<6;++c)for(auto b:std::vector<std::size_t>{11,17})
        f.corrected[c*fft_length+b]+=.3*response(o,-31)[c]+AoaComplex(.01*c,-.013*b);
    for(int k=1;k<=3;++k) {
        o.joint_paths=k;Analysis a;a.bins={11,17};JointPool actual;
        analyze_joint_search(o,f,a,{},&actual);
        std::vector<JointPoolEntry> expected;
        for(std::size_t i=0;i<o.angle_count;++i)
          for(std::size_t j=(k>1?i+1:0);j<(k>1?o.angle_count:1);++j)
            for(std::size_t l=(k>2?j+1:0);l<(k>2?o.angle_count:1);++l) {
                std::array<std::size_t,3> ix{{i,j,l}};
                const double loss=qr_loss(o,f,a.bins,ix,k);
                if(std::isfinite(loss))expected.push_back({loss,ix});
            }
        std::sort(expected.begin(),expected.end());expected.resize(std::min<std::size_t>(30,expected.size()));
        assert(actual.top.size()==expected.size());
        for(std::size_t r=0;r<expected.size();++r) {
            assert(actual.top[r].index==expected[r].index);
            assert(close_enough(actual.top[r].loss,expected[r].loss,a.joint.energy));
        }
        // Scan-facing inputs cannot affect the residual pool or membership.
        o.scan_ml_weight=1;o.scan_min_snr_db=1000;o.scan_candidate_gap_db=0;
        o.min_path_ratio=.999;o.max_bearing_residual=1e-20;Analysis changed;changed.bins=a.bins;JointPool other;
        analyze_joint_search(o,f,changed,{},&other);
        assert(other.top.size()==actual.top.size());
        for(std::size_t r=0;r<other.top.size();++r) {
            assert(other.top[r].index==actual.top[r].index);assert(other.top[r].loss==actual.top[r].loss);
        }
    }
}
void scan() {
    const auto o=options();auto f=signal(o,17.237,true);const auto original=f;
    for(auto angle:std::vector<double>{-60,-31.7,0,17.237,59.99})
        assert(close_enough(v12_scan_power(o,f,{11,17},angle),direct_power(o,f,{11,17},angle)));
    Analysis a;a.bins={11,17};a.direction_frames=4;v12_scan(o,f,a,{});
    for(std::size_t i=0;i<a.beam_scan.angles_deg.size();++i)
        assert(close_enough(a.beam_scan.normalized_power[i]*a.beam_scan.input_power,
            direct_power(o,f,a.bins,a.beam_scan.angles_deg[i])));
    assert(close_enough(a.beam_scan.peak_normalized_power*a.beam_scan.input_power,
        direct_power(o,f,a.bins,a.beam_scan.peak_angle_deg)));
    assert(f.corrected==original.corrected && f.snapshots==original.snapshots && f.raw==original.raw);
    // A wrong phase-only or incoherent objective cannot recover this off-grid peak.
    f=signal(o,17.237,false);Analysis pure;pure.bins={11,17};pure.direction_frames=4;v12_scan(o,f,pure,{});
    assert(pure.beam_scan.valid && std::abs(pure.beam_scan.peak_angle_deg-17.237)<1e-5);
    assert(std::abs(pure.v12.local_angle_deg-17.2)<1e-8);
    assert(std::abs(pure.beam_scan.peak_angle_deg-pure.v12.coarse_angle_deg)<=1);
}
GroupEstimate group(int id,double a,double b) {
    GroupEstimate g;g.id=id;g.has_best=true;g.energy=1;g.residual=.01;g.angle={a,b};
    g.channel={std::size_t(id*3),std::size_t(id*3+1),std::size_t(id*3+2)};
    g.active={true,true};g.amplitude_rms={1,1};g.phase_first_bin_deg={0,0};return g;
}
Options geometry() {
    Options o;for(std::size_t c=0;c<6;++c)o.antenna[c].x=c<3?-1:1;return o;
}
Analysis position(const Options& o,std::array<double,2> a,std::array<double,2> b,double reference) {
    Analysis r;r.groups={group(0,a[0],a[1]),group(1,b[0],b[1])};
    r.v12.enabled=r.v12.valid=true;r.v12.angle_deg=reference;localize_v12(o,r);return r;
}
void locations() {
    auto o=geometry();auto r=position(o,{10,60},{-40,0},20);
    assert(r.v12.intersections.size()==4 && r.v12.selected_id=="C1");
    // Equal path energies keep C1 by stable ID order. A joint reference does
    // not rerank the valid POSITION candidates.
    assert(std::abs(10-20)<std::abs(60-20) && std::abs(0-20)<std::abs(-40-20));
    for(const auto& c:r.v12.intersections) {
        assert(close_enough(c.energy_share,.25) && std::isnan(c.angle_error_deg));
    }
    const double expected=std::atan((std::tan(60*pi/180)+std::tan(-40*pi/180))/2)*180/pi;
    assert(close_enough(r.v12.intersections[2].bearing_deg,expected));
    r=position(o,{10,30},{10,0},15);
    assert(!r.v12.intersections[0].fix.valid && r.v12.intersections[0].fix.status=="near_parallel");
    r=position(o,{-30,-20},{20,30},0);
    for(const auto& c:r.v12.intersections)assert(!c.fix.valid && c.fix.status=="behind_station");
    assert(r.v12.selected_id.empty() && !r.localization.valid);
    r=position(o,{10,60},{-40,0},20);r.v12.valid=false;localize_v12(o,r);
    assert(r.v12.intersections.size()==4 && r.v12.selected_id=="C1" && r.localization.valid);
    for(const auto& c:r.v12.intersections)assert(std::isnan(c.angle_error_deg));
    // Geometry below the array origin retains its drawable bearing independently
    // of a stale or opposing joint angle.
    const double tx=std::tan(-179*pi/180)*-10,ty=-10;
    const double a=std::atan2(tx+1,ty)*180/pi,b=std::atan2(tx-1,ty)*180/pi;
    for(auto& ant:o.antenna)ant.y=20; // Point y=10; rear bearing relative to O remains drawable.
    r=position(o,{a,a},{b,b},179);
    assert(r.v12.selected_id=="C1" && close_enough(r.v12.intersections[0].bearing_deg,-179));
    assert(std::isnan(r.v12.intersections[0].angle_error_deg));
    for(auto& ant:o.antenna)ant.x=0;
    r=position(o,{10,60},{-40,0},20);
    for(const auto& c:r.v12.intersections)assert(c.fix.status=="coincident_centres");
    o=geometry();r=position(o,{10,60},{-40,0},20);
    r.groups[0].active[0]=false;localize_v12(o,r);
    assert(!r.v12.intersections[0].fix.valid && r.v12.intersections[0].fix.status=="weak_path");
    assert(std::isfinite(r.v12.intersections[0].fix.x) && std::isfinite(r.v12.intersections[0].bearing_deg));
    r=position(o,{10,60},{-40,0},20);r.groups[0].ambiguous=true;localize_v12(o,r);
    for(const auto& c:r.v12.intersections)assert(!c.fix.valid && c.fix.status=="ambiguous_fit" && std::isfinite(c.fix.x));
    assert(r.v12.selected_id.empty());
    r.groups[0].ambiguous=false;r.groups[0].raw_validated=true;r.groups[0].raw_residual_ratio=.2;localize_v12(o,r);
    for(const auto& c:r.v12.intersections)assert(!c.fix.valid && c.fix.status=="poor_fit" && std::isfinite(c.fix.x));
    o.min_crossing_angle_deg=0;r=position(o,{10,10},{10,10},20);
    for(const auto& c:r.v12.intersections)assert(c.fix.status=="near_parallel");
    for(auto& ant:o.antenna)ant.y=-100;
    r=position(o,{10,60},{-40,0},20);
    for(const auto& c:r.v12.intersections)assert(c.fix.status=="behind_station");
}
aoa_console::Json packet(Options o,Analysis a) {
    CaptureState c;c.mode="fft_file";CaptureBatch b;b.run_id="independent_review";
    b.completed=4;b.handoff_status="disabled";a.complete=true;a.direction_frames=4;
    auto p=console_packet(o,c,b,a);p["schema"]=1;p["source"]="aoa";p["session"]="independent_review";p["seq"]=1;
    return p;
}
void integration() {
    auto o=options();const auto f=signal(o,17.237,true);
    Analysis a;a.bins={11,17};a.direction_frames=4;analyze_v12(o,f,f,a,{});
    assert(a.v12.valid);a.status="ok";
    assert(a.v12.angle_deg==a.joint.angle.at(a.selection.path_index));
    assert(close_enough(a.v12.score,.25*(1-a.v12.residual_ratio)+.75*a.v12.main_match));
    // Independently reconstruct every original pool and maximize the required
    // score over all retained paths, including paths distant from the scan.
    double max_score=-1;int expected_k=0,expected_p=0,expected_rank=0;
    std::array<std::size_t,3> expected_ix{};
    double energy=0;for(auto b:a.bins)for(std::size_t c=0;c<6;++c)energy+=std::norm(f.corrected[c*fft_length+b]);
    for(int order=1;order<=3;++order) {
        std::vector<JointPoolEntry> candidates;
        for(std::size_t i=0;i<o.angle_count;++i)
          for(std::size_t j=(order>1?i+1:0);j<(order>1?o.angle_count:1);++j)
            for(std::size_t l=(order>2?j+1:0);l<(order>2?o.angle_count:1);++l) {
                const std::array<std::size_t,3> ix{{i,j,l}};
                const auto loss=qr_loss(o,f,a.bins,ix,order);
                if(std::isfinite(loss))candidates.push_back({loss,ix});
            }
        std::sort(candidates.begin(),candidates.end());candidates.resize(std::min<std::size_t>(30,candidates.size()));
        for(std::size_t rank=0;rank<candidates.size();++rank)for(int p=0;p<order;++p) {
            const auto& candidate=candidates[rank];
            const auto angle=o.angle_start+candidate.index[p]*o.angle_step;
            const auto du=std::sin(angle*pi/180)-std::sin(a.v12.scan.peak_angle_deg*pi/180);
            const auto s=.25*(1-candidate.loss/energy)+.75*std::exp(-.5*du*du/(.05*.05));
            if(s>max_score) {max_score=s;expected_k=order;expected_p=p;expected_rank=int(rank)+1;expected_ix=candidate.index;}
        }
    }
    assert(a.selection.model_paths==expected_k && a.selection.path_index==expected_p && a.v12.selected_rank==expected_rank);
    assert(a.v12.angle_deg==o.angle_start+expected_ix[expected_p]*o.angle_step);
    assert(close_enough(a.v12.score,max_score));
    for(int k=1;k<=3;++k)assert(a.v12.pool_counts[k-1]==std::min<std::size_t>(30,joint_combinations(o.angle_count,k)));
    const auto main=a.v12.angle_deg,score=a.v12.score;const auto k=a.selection.model_paths,p=a.selection.path_index,rank=a.v12.selected_rank;
    for(auto second:std::vector<double>{-50,0,50,std::numeric_limits<double>::quiet_NaN()}) {
        auto alt=a;alt.v12.secondary_angle_deg=second;v12_attach_secondary(alt);
        assert(alt.v12.angle_deg==main && alt.v12.score==score && alt.selection.model_paths==k &&
               alt.selection.path_index==p && alt.v12.selected_rank==rank && alt.joint.angle==a.joint.angle);
        if(std::isfinite(second) && k>1) {
            assert(alt.v12.secondary_ml_angle_deg!=main);
            assert(close_enough(alt.v12.secondary_score,v12_match(alt.v12.secondary_ml_angle_deg,second,.1)));
        }
    }
    o.bins={11,17};const auto pipeline_angle=analyze(o,f,{});o.processing_mode="scan6";
    const auto scan_mode=analyze(o,f,{});
    assert(scan_mode.v12.valid && scan_mode.v12.angle_deg==pipeline_angle.v12.angle_deg);
    auto po=o;po.processing_mode="position";const auto pos=analyze(po,f,{});
    assert(pos.v12.position_energy && !pos.v12.valid && !pos.joint.has_best);
    assert((pos.v12.pool_counts==std::array<std::size_t,3>{{0,0,0}}));
    assert(std::isnan(pos.v12.angle_deg));
    assert(pos.joint_score.empty() && pos.v12.scan.angles_deg.empty() && pos.v12.scan.relative_db.empty());
    assert(pos.groups.size()==2 && !pos.groups[0].display_score.empty() && !pos.groups[1].display_score.empty());
    assert(!packet(po,pos)["joint"].contains("score_grid"));
    auto ambiguous=pos;for(auto& g:ambiguous.groups)g.ambiguous=true;localize_v12(po,ambiguous);
    assert(ambiguous.v12.position_energy && !ambiguous.v12.valid && ambiguous.v12.selected_id.empty());
    assert(std::any_of(ambiguous.v12.intersections.begin(),ambiguous.v12.intersections.end(),[](const auto& c){
        return !c.fix.valid && c.fix.status=="ambiguous_fit" && std::isfinite(c.fix.x) && std::isfinite(c.bearing_deg); }));
    auto raw_bad=pos;for(auto& g:raw_bad.groups){g.raw_validated=true;g.raw_residual_ratio=g.latest_residual_ratio=.2;}
    localize_v12(po,raw_bad);assert(raw_bad.v12.position_energy && !raw_bad.v12.valid && raw_bad.v12.selected_id.empty());
    assert(std::any_of(raw_bad.v12.intersections.begin(),raw_bad.v12.intersections.end(),[](const auto& c){
        return !c.fix.valid && c.fix.status=="poor_fit" && std::isfinite(c.fix.x) && std::isfinite(c.bearing_deg); }));
    auto no=geometry();no.angle_start=po.angle_start;no.angle_step=po.angle_step;no.angle_count=po.angle_count;
    Analysis no_reference;no_reference.v12.enabled=true;no_reference.groups={group(0,10,60),group(1,-40,0)};
    no_reference.status="ok";localize_v12(no,no_reference);
    assert(no_reference.v12.selected_id=="C1" && no_reference.v12.intersections[0].fix.valid);
    // Degenerate higher orders force K1. A visible second scan still has no
    // remaining same-group ML path; serialization must preserve null.
    o=options();for(auto& ant:o.antenna)ant.x=ant.y=0;o.angle_start=-1;o.angle_step=1;o.angle_count=3;
    const auto single=signal(o,0,false);Analysis one;one.bins={11};one.direction_frames=4;
    analyze_v12(o,single,single,one,{});assert(one.v12.valid && one.selection.model_paths==1);
    one.v12.secondary_angle_deg=45;v12_attach_secondary(one);one.status="ok";
    assert(std::isnan(one.v12.secondary_ml_angle_deg));
    assert(aoa_console::Json::parse(v12_json(one))["joint"]["secondary"]["ml_angle_deg"].is_null());
    Analysis empty;empty.v12.enabled=true;empty.status="no_signal";empty.beam_scan.status="no_signal";
}
}
int main() {
    pools();scan();locations();integration();
    std::cout<<"PASS independent QR Top30, full-complex scan, secondary isolation, four-point localization\n";
}
