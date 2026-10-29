#ifdef NDEBUG
#undef NDEBUG
#endif
#include "app.hpp"
#include <cassert>
#include <cmath>
#include <iostream>

using namespace aoa_app;
namespace {
Options options() {
    Options o;o.processing_mode="position";o.rf_hz=445e6;
    const double x[]={-1.2823613348729794,-.9361806674364898,-.59,
                       .59,.9361806674364898,1.2823613348729794};
    for(std::size_t ch=0;ch<channels;++ch) o.antenna[ch].x=x[ch];
    return o;
}
Analysis geometry() {
    Analysis a;a.complete=true;a.status="ok";a.v12.enabled=a.v12.valid=true;
    a.v12.angle_deg=-11;
    for(int id=0;id<2;++id) {
        GroupEstimate g;g.id=id;g.has_best=true;g.energy=1;g.residual=.001;
        g.channel=id?std::vector<std::size_t>{3,4,5}:std::vector<std::size_t>{0,1,2};
        g.angle=id?std::vector<double>{-35,-12}:std::vector<double>{-8,42};
        g.active={true,true};g.amplitude_rms=id?std::vector<double>{1,.617}:std::vector<double>{1,.136};
        a.groups.push_back(g);
    }
    return a;
}
void equal(double actual,double expected,double tolerance=2e-12) {
    assert(std::isfinite(actual) && std::abs(actual-expected)<tolerance);
}
void energy_and_independence() {
    const auto o=options();auto a=geometry();localize_v12(o,a);
    assert(a.v12.position_energy && a.localization.valid && a.v12.selected_id=="C1");
    equal(a.localization.x,-1.406359,1e-6);equal(a.localization.y,3.345494,1e-6);
    double sum=0;
    for(std::size_t i=0;i<4;++i) {
        const auto& c=a.v12.intersections[i];
        assert(c.id=="C"+std::to_string(i+1) && c.fix.valid);
        const double p=i/2?std::pow(.136,2):1,q=i%2?std::pow(.617,2):1;
        equal(c.energy_share,p*q/((1+.136*.136)*(1+.617*.617)));
        sum+=c.energy_share;
        assert(std::isnan(c.angle_error_deg) && std::isnan(c.selection_cost));
        assert(std::isnan(c.direction_residual_ratio) && std::isnan(c.angle_cost));
    }
    equal(sum,1);
    // Neither a stale joint reference nor its validity controls POSITION.
    for(const double angle:{-80.0,0.0,80.0,std::numeric_limits<double>::quiet_NaN()}) {
        auto b=geometry();b.v12.valid=false;b.v12.angle_deg=angle;
        localize_v12(o,FftData{},b);
        assert(b.localization.valid && b.v12.selected_id==a.v12.selected_id);
        for(std::size_t i=0;i<4;++i) equal(b.v12.intersections[i].energy_share,a.v12.intersections[i].energy_share);
    }
    // Independent group gain cannot change shares, including extreme finite scales.
    for(const double scale:{1e-300,.013,123.0,1e308}) {
        auto b=geometry();for(auto& x:b.groups[0].amplitude_rms)x*=scale;
        for(auto& x:b.groups[1].amplitude_rms)x*=scale;
        localize_v12(o,b);assert(b.v12.selected_id==a.v12.selected_id);
        for(std::size_t i=0;i<4;++i) equal(b.v12.intersections[i].energy_share,a.v12.intersections[i].energy_share);
    }
    auto tied=geometry();for(auto& g:tied.groups)g.amplitude_rms={1,1};
    localize_v12(o,tied);assert(tied.v12.selected_id=="C1");
    for(const auto& c:tied.v12.intersections)equal(c.energy_share,.25);
}
void quality_and_geometry_gates() {
    const auto o=options();auto a=geometry();a.groups[0].active[0]=false;localize_v12(o,a);
    assert(a.v12.selected_id=="C3" && a.localization.valid);
    assert(a.v12.intersections[0].fix.status=="weak_path" && !a.v12.intersections[0].fix.valid);
    double sum=0;for(const auto& c:a.v12.intersections)sum+=c.energy_share;equal(sum,1);
    // Invalid highest-energy geometry does not win or redistribute its energy.
    a=geometry();a.groups[0].angle[0]=-35;localize_v12(o,a);
    assert(a.v12.intersections[0].fix.status=="near_parallel");
    assert(a.v12.intersections[0].energy_share>a.v12.intersections[2].energy_share);
    assert(a.localization.valid && a.v12.selected_id=="C3");
    for(int test=0;test<5;++test) {
        a=geometry();
        if(test==0)a.groups[0].ambiguous=true;
        if(test==1)a.groups[1].residual=.2;
        if(test>=2) {
            a.groups[1].raw_validated=true;
            a.groups[1].raw_residual_ratio=test==2?.2:.001;
            a.groups[1].latest_residual_ratio=test==3?.2:.001;
            if(test==4)a.groups[1].raw_residual_ratio=std::numeric_limits<double>::quiet_NaN();
        }
        localize_v12(o,a);assert(!a.localization.valid && a.v12.selected_id.empty());
        for(const auto& c:a.v12.intersections)assert(!c.fix.valid && std::isfinite(c.fix.x));
    }
    a=geometry();a.groups[1].has_best=false;localize_v12(o,a);
    assert(a.v12.selected_id.empty() && a.localization.status=="no_valid_intersection");
    for(const auto& c:a.v12.intersections)assert(c.fix.status=="missing_bearing");
}
void malformed_amplitudes() {
    const auto o=options();
    const double nan=std::numeric_limits<double>::quiet_NaN(),inf=std::numeric_limits<double>::infinity();
    for(const auto& amp:std::vector<std::vector<double>>{{},{1},{1,2,3},{0,0},{-1,1},{nan,1},{1,inf}}) {
        auto a=geometry();a.groups[0].amplitude_rms=amp;localize_v12(o,a);
        assert(!a.localization.valid && a.v12.selected_id.empty());
        for(const auto& c:a.v12.intersections) {
            assert(!c.fix.valid && c.fix.status=="unknown_power" && std::isnan(c.energy_share));
            assert(std::isfinite(c.fix.x) && std::isfinite(c.fix.y));
        }
    }
    auto a=geometry();a.groups[0].amplitude_rms={0,1};localize_v12(o,a);
    assert(a.localization.valid && a.v12.selected_id=="C3");
}
}
int main() {
    energy_and_independence();quality_and_geometry_gates();malformed_amplitudes();
    std::cout<<"PASS original triad ML energy, joint independence, quality gates and finite-scale invariance\n";
}
