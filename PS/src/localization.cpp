#include "app.hpp"
#include <algorithm>
#include <cmath>

namespace aoa_app {
std::array<std::array<double, 2>, 2> group_centres(const Options& o) {
    std::array<std::array<double, 2>, 2> centre{};
    for (std::size_t g = 0; g < 2; ++g) for (auto ch : o.groups[g]) {
        centre[g][0] += o.antenna[ch].x / 3;
        centre[g][1] += o.antenna[ch].y / 3;
    }
    return centre;
}

Localization localize(const Options& o, const std::vector<GroupEstimate>& groups) {
    Localization fix;
    // Two paths in one triad are two incident waves, not the two stations of a fix.
    if (o.paths != 1) { fix.status = "requires_single_path"; return fix; }
    std::array<const GroupEstimate*, 2> bearing{};
    for (const auto& group : groups)
        if (group.id >= 0 && group.id < 2) bearing[static_cast<std::size_t>(group.id)] = &group;
    for (std::size_t g = 0; g < 2; ++g) {
        if (!bearing[g] || !bearing[g]->has_best || bearing[g]->angle.size() != 1 ||
            bearing[g]->active.size() != 1 || !bearing[g]->active[0]) {
            fix.status = "missing_bearing"; return fix;
        }
        fix.angle[g] = bearing[g]->angle[0];
    }
    for (const auto* group : bearing) {
        if(group->raw_validated && (!std::isfinite(group->raw_residual_ratio) ||
           !std::isfinite(group->latest_residual_ratio) ||
           group->raw_residual_ratio>o.max_bearing_residual ||
           group->latest_residual_ratio>o.max_bearing_residual)) {
            fix.status="poor_bearing_fit";return fix;
        }
        if (group->ambiguous) { fix.status = "ambiguous_bearing"; return fix; }
        if (!(group->energy > 0) || !std::isfinite(group->residual / group->energy) ||
            group->residual / group->energy > o.max_bearing_residual) {
            fix.status = "poor_bearing_fit"; return fix;
        }
    }
    const auto centre = group_centres(o);
    const double dx = centre[1][0] - centre[0][0], dy = centre[1][1] - centre[0][1];
    if (!std::isfinite(dx) || !std::isfinite(dy)) {
        fix.status = "nonfinite_geometry"; return fix;
    }
    if (std::hypot(dx, dy) <= 1e-9) { fix.status = "coincident_centres"; return fix; }
    const double a0 = fix.angle[0] * pi / 180, a1 = fix.angle[1] * pi / 180;
    const double ux0 = std::sin(a0), uy0 = std::cos(a0);
    const double ux1 = std::sin(a1), uy1 = std::cos(a1);
    const double determinant = ux0 * uy1 - uy0 * ux1;
    const double dot = ux0 * ux1 + uy0 * uy1;
    fix.crossing_angle_deg = std::atan2(std::abs(determinant), std::abs(dot)) * 180 / pi;
    // Singular values of [u0,-u1] give (1+|u0.u1|)/|cross(u0,u1)|.
    if (std::abs(determinant) > 0)
        fix.geometry_condition = (1 + std::abs(dot)) / std::abs(determinant);
    if (std::abs(determinant) < std::sin(o.min_crossing_angle_deg * pi / 180)) {
        fix.status = "near_parallel"; return fix;
    }
    fix.range[0] = (dx * uy1 - dy * ux1) / determinant;
    fix.range[1] = (dx * uy0 - dy * ux0) / determinant;
    if (!std::isfinite(fix.range[0]) || !std::isfinite(fix.range[1])) {
        fix.status = "nonfinite_geometry"; return fix;
    }
    if (fix.range[0] <= 0 || fix.range[1] <= 0) {
        fix.status = "behind_station"; return fix;
    }
    fix.x = centre[0][0] + fix.range[0] * ux0;
    fix.y = centre[0][1] + fix.range[0] * uy0;
    if (!std::isfinite(fix.x) || !std::isfinite(fix.y)) {
        fix.x = fix.y = std::numeric_limits<double>::quiet_NaN();
        fix.status = "nonfinite_geometry"; return fix;
    }
    fix.valid = true; fix.status = "ok";
    return fix;
}
// Match the original PC candidate contract: compute geometry first, retain
// finite coordinates for weak/ambiguous/poor fits, then select only valid points.
void localize_v12(const Options& o,Analysis& result) {
    auto& v=result.v12;
    v.enabled=true;v.position_energy=true;
    v.intersections.clear();v.selected_id.clear();
    std::array<const GroupEstimate*,2> groups{};
    for(const auto& g:result.groups) if(g.id>=0 && g.id<2) groups[g.id]=&g;
    std::array<std::array<double,2>,2> shares{};
    for(std::size_t g=0;g<groups.size();++g) {
        shares[g].fill(std::numeric_limits<double>::quiet_NaN());
        if(!groups[g] || groups[g]->amplitude_rms.size()!=2) continue;
        const auto& amp=groups[g]->amplitude_rms;
        if(!std::isfinite(amp[0]) || !std::isfinite(amp[1]) || amp[0]<0 || amp[1]<0) continue;
        const double peak=std::max(amp[0],amp[1]);
        if(!(peak>0)) continue;
        const double p0=std::pow(amp[0]/peak,2),p1=std::pow(amp[1]/peak,2);
        shares[g]={{p0/(p0+p1),p1/(p0+p1)}};
    }
    const auto centre=group_centres(o);
    const double dx=centre[1][0]-centre[0][0],dy=centre[1][1]-centre[0][1];
    double ox=0,oy=0;for(const auto& a:o.antenna) {ox+=a.x/6;oy+=a.y/6;}
    auto intersect=[&](int i,int j) {
        Localization fix;
        if(!groups[0] || !groups[1] || !groups[0]->has_best || !groups[1]->has_best ||
           std::size_t(i)>=groups[0]->angle.size() || std::size_t(j)>=groups[1]->angle.size()) {
            fix.status="missing_bearing";return fix;
        }
        fix.angle={{groups[0]->angle[i],groups[1]->angle[j]}};
        const double a=fix.angle[0]*pi/180,b=fix.angle[1]*pi/180;
        const double ux=std::sin(a),uy=std::cos(a),vx=std::sin(b),vy=std::cos(b);
        const double det=ux*vy-uy*vx,dot=ux*vx+uy*vy;
        fix.crossing_angle_deg=std::atan2(std::abs(det),std::abs(dot))*180/pi;
        if(std::abs(det)>0) fix.geometry_condition=(1+std::abs(dot))/std::abs(det);
        if(!std::isfinite(dx) || !std::isfinite(dy) || !std::isfinite(det)) {
            fix.status="nonfinite";return fix;
        }
        if(std::hypot(dx,dy)<=1e-9) {fix.status="coincident_centres";return fix;}
        if(fix.crossing_angle_deg<o.min_crossing_angle_deg || std::abs(det)<1e-12) {
            fix.status="near_parallel";return fix;
        }
        fix.range={{(dx*vy-dy*vx)/det,(dx*uy-dy*ux)/det}};
        const double x=centre[0][0]+fix.range[0]*ux,y=centre[0][1]+fix.range[0]*uy;
        if(fix.range[0]<=0 || fix.range[1]<=0 || y<0) {fix.status="behind_station";return fix;}
        if(!std::isfinite(x) || !std::isfinite(y)) {fix.status="nonfinite";return fix;}
        fix.x=x;fix.y=y;
        if(std::size_t(i)>=groups[0]->active.size() || !groups[0]->active[i] ||
           std::size_t(j)>=groups[1]->active.size() || !groups[1]->active[j]) {
            fix.status="weak_path";return fix;
        }
        if(groups[0]->ambiguous || groups[1]->ambiguous) {fix.status="ambiguous_fit";return fix;}
        for(const auto* group:groups) {
            // The original PC treats a null fused residual (no positive energy)
            // as zero; normal ML candidates always have positive finite energy.
            const double ratio=group->energy>0?group->residual/group->energy:0;
            if(std::isfinite(ratio) && ratio>o.max_bearing_residual) {fix.status="poor_fit";return fix;}
        }
        for(const auto* group:groups) if(group->raw_validated &&
            (!std::isfinite(group->raw_residual_ratio) || !std::isfinite(group->latest_residual_ratio) ||
             group->raw_residual_ratio>o.max_bearing_residual || group->latest_residual_ratio>o.max_bearing_residual)) {
            fix.status="poor_fit";return fix;
        }
        fix.valid=true;fix.status="ok";return fix;
    };
    for(int i=0;i<2;++i) for(int j=0;j<2;++j) {
        V12Intersection candidate;candidate.id="C"+std::to_string(i*2+j+1);candidate.paths={{i,j}};
        candidate.fix=intersect(i,j);
        candidate.energy_share=shares[0][i]*shares[1][j];
        if(candidate.fix.valid && !std::isfinite(candidate.energy_share)) {
            candidate.fix.valid=false;candidate.fix.status="unknown_power";
        }
        if(std::isfinite(candidate.fix.x) && std::isfinite(candidate.fix.y)) {
            candidate.bearing_deg=std::atan2(candidate.fix.x-ox,candidate.fix.y-oy)*180/pi;
        }
        v.intersections.push_back(std::move(candidate));
    }
    result.localization=Localization{};result.localization.status="no_valid_intersection";
    double best=-1;
    for(const auto& c:v.intersections) if(c.fix.valid && c.energy_share>best) {
        best=c.energy_share;result.localization=c.fix;v.selected_id=c.id;
    }
}

// Compatibility entry point: POSITION consumes the original triad ML fits only.
void localize_v12(const Options& o,const FftData&,Analysis& result) {
    localize_v12(o,result);
}
} // namespace aoa_app
